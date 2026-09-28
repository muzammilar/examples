.PHONY: up test status cli benchmark down

SMOKE ?=

COMPOSE := docker compose --progress quiet
NGQL := $(COMPOSE) run --rm -T console

# storaged must be registered with metad (ADD HOSTS) before it reports healthy
up:
	$(COMPOSE) up --detach --wait metad graphd
	$(NGQL) -e 'ADD HOSTS "storaged":9779' | grep -E 'succeeded|ERROR'
	$(COMPOSE) up --detach --wait
	until $(NGQL) -e 'SHOW HOSTS' | grep ONLINE >/dev/null; do sleep 1; done
	@echo "storaged online"

# space + schema + index, a small follows graph, then GO / LOOKUP / MATCH / FIND PATH.
# '#' lines are stripped: the console would send them as empty statements.
test:
	for f in ngql/*.ngql; do echo "-- $$f"; grep -v '^#' $$f | $(NGQL) -f /dev/stdin \
	  | awk '/UTC$$|^Bye|^$$/ {next} {print} /^\[ERROR/ {e=1} END {exit e}' || exit 1; done

status:
	$(NGQL) -e 'SHOW HOSTS' | grep '^[+|]'

cli:
	$(COMPOSE) run --rm console

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 6g
BENCH_SPEC ?= metad=0.5:512m graphd=1.5:2g storaged=2:3.5g
export BENCH_CLIENT_CPUS ?= 2

# needs `make up`; SMOKE=1 for 10k persons (default 100k), or set N
benchmark:
	@$(COMPOSE) ps --status running --services | grep -qx graphd || { echo 'run `make up` first'; exit 1; }
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS; SMOKE=$(SMOKE) HOST_UID=$$(id -u) HOST_GID=$$(id -g) $(COMPOSE) run --rm bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

down:
	$(COMPOSE) --profile tools down --volumes
