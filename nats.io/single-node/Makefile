.PHONY: up test app benchmark status cli down

up:
	docker compose up --detach --wait

# core pub/sub + request/reply, a JetStream stream + pull consumer, a KV bucket
test:
	for f in scripts/*.sh; do echo "-- $$f"; docker compose --progress quiet run --rm -T tools sh -ex /$$f 2>&1 || exit 1; done

# the same concepts from Python (nats-py, asyncio); image is (re)built from app/
app:
	docker compose --progress quiet --profile tools build app
	for f in app/*.py; do echo "-- $$f"; docker compose --progress quiet run --rm -T app python $$(basename $$f) 2>&1 || exit 1; done

# `nats bench`: core pub/sub 1x1 and CLIENTSxCLIENTS at 128 B and 1 KiB (MSGS messages each),
# request/reply (REQ_MSGS), JetStream file/R1 publish sync (JS_SYNC_MSGS) and async + pull fetch
# (JS_MSGS); SMOKE=1 runs a tenth of the messages. Prints a summary table, keeps
# results/nats-<UTC time>.{txt,json}; the bench stream is removed afterwards.
SMOKE ?=
S := $(if $(filter 1,$(SMOKE)),10,1)
MSGS ?= $(shell echo $$((1000000 / $(S))))
CLIENTS ?= 4
REQ_MSGS ?= $(shell echo $$((20000 / $(S))))
JS_MSGS ?= $(shell echo $$((500000 / $(S))))
JS_SYNC_MSGS ?= $(shell echo $$((20000 / $(S))))

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= nats
export BENCH_CLIENT_CPUS ?= 2

benchmark:
	@docker compose ps --status running --services | grep -qx nats || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=nats-$$(date -u +%Y%m%dT%H%M%SZ) MSGS=$(MSGS) CLIENTS=$(CLIENTS) REQ_MSGS=$(REQ_MSGS) \
		JS_MSGS=$(JS_MSGS) JS_SYNC_MSGS=$(JS_SYNC_MSGS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose --progress quiet run --rm -T bench && docker compose --progress quiet run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose --progress quiet run --rm -T tools nats server check jetstream

cli:
	docker compose --progress quiet run --rm tools

down:
	docker compose --profile tools --profile bench down --volumes --rmi local
