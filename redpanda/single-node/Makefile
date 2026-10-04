.PHONY: up test status cli console benchmark logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

up:
	docker compose up --detach --wait
	$(MAKE) --no-print-directory status

# scripts/01-03 run in the broker container (rpk, curl), 04 in the console container
test:
	@set -e; for f in scripts/0[1-3]-*.sh; do echo "==== $$f"; docker exec redpanda-sn-0 bash /$$f; done
	@echo "==== scripts/04-console.sh"; docker exec -i redpanda-sn-console sh -s < scripts/04-console.sh

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec redpanda-sn-0 rpk cluster health
	docker exec redpanda-sn-0 rpk cluster license info

cli:
	docker exec -it redpanda-sn-0 bash

console:
	@echo "Redpanda Console: http://localhost:$${CONSOLE_PORT:-8080}"

# rpk produce + consume of RECORDS records of RECORD_SIZE bytes (bench/run.sh), broker capped by
# bench/limits.sh to BENCH_CPUS / BENCH_MEM (matches --smp 2 --memory 2G plus headroom)
RECORDS ?= 1000000
RECORD_SIZE ?= 1000
PARTITIONS ?= 6
BENCH_CPUS ?= 2
BENCH_MEM ?= 3g
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx redpanda || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) redpanda) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=redpanda-single-$$(date -u +%Y%m%dT%H%M%SZ) RECORDS=$(RECORDS) RECORD_SIZE=$(RECORD_SIZE) PARTITIONS=$(PARTITIONS) \
		HOST_INFO="$$(uname -sm)" DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench; rc=$$?; sh bench/limits.sh restore; exit $$rc

logs:
	docker compose logs -f

down:
	docker compose --profile bench down --volumes --remove-orphans --rmi local
