.PHONY: license up test status cli benchmark down

# q in the server's container, a second process talking to the server over IPC
QEXEC := docker exec -i kdb-single q

# up/test/benchmark need a kdb+ license: KDB_LICENSE_B64 / KDB_LICENSE_K4B64 in the environment,
# or kc.lic / k4.lic in ../license (see ../README.md#license-required-before-running)
license:
	@[ -n "$$KDB_LICENSE_B64$$KDB_LICENSE_K4B64" ] || [ -f ../license/kc.lic ] || [ -f ../license/k4.lic ] || \
		{ echo 'no kdb+ license: export KDB_LICENSE_B64=<base64 kc.lic> or KDB_LICENSE_K4B64=<base64 k4.lic>, or copy kc.lic / k4.lic to kdb/license/ (see kdb/README.md#license-required-before-running)'; exit 1; }

up: license
	docker compose up --detach --wait --build

# q/walkthrough.q line by line through q/run.q: tables, q-sql, aj, wj, attributes, splayed and
# partitioned tables under /data/db
test:
	$(QEXEC) /q/run.q /q/walkthrough.q -q

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	$(QEXEC) /q/status.q -q

cli:
	docker exec -it kdb-single q

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= kdb
export BENCH_CLIENT_CPUS ?= 2

# bench/bench.q against the running server: N trades (default 10M; SMOKE=1: 1M) and 2N quotes
SMOKE ?=
N ?=
benchmark: license
	@docker compose ps --status running --services | grep -qx kdb || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS SMOKE=$(SMOKE) N=$(N) \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes, {{.Architecture}}')"; \
	docker compose run --rm -T --build bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	docker compose --profile bench down --volumes --rmi local
