.PHONY: up test failover benchmark status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

D1 = localhost:$${DATA1_PORT:-8386}
D2 = localhost:$${DATA2_PORT:-8387}
CTL = docker exec influxdb1-meta-1 influxd-ctl
# InfluxQL against a data node: $(call Q,host:port,query)
Q = curl -sS -G -H 'Accept: application/csv' "$(1)/query" --data-urlencode db=fleet --data-urlencode "q=$(2)"; echo

# 3 meta + 2 data nodes, then the `join` one-shot adds them all to the cluster
up:
	docker compose up --detach --wait meta-1 meta-2 meta-3 data-1 data-2
	docker compose up --no-log-prefix join | grep -v 'Added'

# scripts/demo.sh: RF 2 database, writes with consistency all/quorum, reads from both data nodes, shards
test:
	docker compose --progress quiet run --rm --no-deps client

# stop data-1: through data-2 a consistency=one write succeeds and is queued for data-1 (hinted
# handoff); consistency=all answers 500 "partial write" yet keeps the point (no rollback). Reads stay
# complete (RF 2). Then data-1 restarts and data-2 is stopped, so data-1 must answer from its own
# copy: the writes made while it was down arrived through hinted handoff.
failover:
	@echo "==> stopping data-1"; docker compose stop data-1
	@n=$$(date +%s); \
	curl -sS -w 'write via data-2 consistency=one -> HTTP %{http_code}\n' "$(D2)/write?db=fleet&precision=s&consistency=one" \
		--data-binary "cpu,host=host-failover,region=r9 usage=1.5,load=0i $$n"; \
	curl -sS -w ' <- write via data-2 consistency=all -> HTTP %{http_code}\n' "$(D2)/write?db=fleet&precision=s&consistency=all" \
		--data-binary "cpu,host=host-failover-all,region=r9 usage=2.5,load=0i $$n"
	@echo "==> reads through data-2 while data-1 is down"
	@$(call Q,$(D2),SELECT count(usage) FROM cpu)
	@$(call Q,$(D2),SELECT * FROM cpu WHERE region = 'r9')
	@echo "==> starting data-1, waiting for data-2's hinted-handoff queue to drain"; docker compose up --detach --wait data-1
	@for i in $$(seq 60); do [ -z "$$(docker exec influxdb1-data-2 find /var/lib/influxdb/hh -type f -size +0)" ] && break; sleep 1; done
	@echo "==> stopping data-2: data-1 answers alone"; docker compose stop data-2
	@$(call Q,$(D1),SELECT count(usage) FROM cpu)
	@$(call Q,$(D1),SELECT * FROM cpu WHERE region = 'r9')
	@echo "==> starting data-2"; docker compose up --detach --wait data-2

# bench/bench.py: ROWS rows for SERIES hosts into database `bench` (RF 2), writers alternating
# between the data nodes, consistency CONSISTENCY (one/quorum/all); then ITER runs of each query
SMOKE ?=
ROWS ?= $(if $(filter 1,$(SMOKE)),200000,2000000)
SERIES ?= $(if $(filter 1,$(SMOKE)),1000,10000)
BATCH ?= 10000
WRITERS ?= 16
CONSISTENCY ?= one
ITER ?= 30
# resource budget (bench/limits.sh): 4 CPUs / 6 GB for the cluster, 1 CPU / 512 MB per meta
# node off the top, the data nodes share the rest
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= meta-1=0.33:512m meta-2=0.33:512m meta-3=0.33:512m data-1 data-2
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx data-1 || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=influxdb1-cluster-$$(date -u +%Y%m%dT%H%M%SZ) ROWS=$(ROWS) SERIES=$(SERIES) \
		BATCH=$(BATCH) WRITERS=$(WRITERS) CONSISTENCY=$(CONSISTENCY) ITER=$(ITER) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T --no-deps bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	$(CTL) show

# influx CLI on data-1 (InfluxQL shell)
cli:
	docker exec -it influxdb1-data-1 influx -database fleet

down:
	docker compose --profile test --profile bench down --volumes
