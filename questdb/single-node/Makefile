.PHONY: up test benchmark status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

PSQL = docker compose --progress quiet run --rm -T psql -v ON_ERROR_STOP=1 --echo-queries
EXEC = docker exec questdb curl -sfG http://localhost:9000/exec --data-urlencode
# WAL tables apply writes in the background: wait until every table's writer caught up
WAIT_WAL = for i in $$(seq 100); do \
	  $(EXEC) 'query=SELECT count() FROM wal_tables() WHERE writerTxn < sequencerTxn' | grep -q '"dataset":\[\[0\]\]' && break; \
	  sleep 0.2; done

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix questdb | grep -m1 -E 'QuestDB [0-9.]+'
	@echo "web console: http://localhost:$${QUESTDB_HTTP_PORT:-9000}"

# sql/*.sql through psql (PG wire): schema, generated market data, SAMPLE BY, LATEST ON,
# ASOF JOIN, DEDUP; then the same candles re-sent twice as ILP over HTTP (/write)
test:
	for f in sql/0[12]-*.sql; do echo; echo "==== $$f"; $(PSQL) -f /$$f || exit 1; done
	@$(WAIT_WAL)
	for f in sql/0[3-6]-*.sql; do echo; echo "==== $$f"; $(PSQL) -f /$$f || exit 1; done
	@echo; echo "==== ILP over HTTP (/write): the same two lines sent twice into candles; dedup on (ts, symbol)"
	@echo "==== replaces the 10:00 BTC-USD candle once more and adds one 12:00 SOL-USD candle, once"
	for i in 1 2; do printf '%s\n' \
		'candles,symbol=BTC-USD close=65030.0,volume=13.0 1790762400000000000' \
		'candles,symbol=SOL-USD close=151.25,volume=900.0 1790769600000000000' | \
		docker exec -i questdb curl -sf -XPOST http://localhost:9000/write --data-binary @- && echo "sent $$i: HTTP 204"; done
	@$(WAIT_WAL)
	$(PSQL) -c 'SELECT * FROM candles ORDER BY ts, symbol' \
		-c "SELECT name, writerTxn, sequencerTxn, suspended FROM wal_tables() ORDER BY name"

# bench/bench.py: ROWS rows over ILP/HTTP from PROCS senders (default 20M from 8; SMOKE=1: 3M),
# then timed queries; prints a table, keeps results/questdb-single-<UTC time>.json and drops
# the bench tables afterwards (KEEP=1 keeps them)
SMOKE ?=
ROWS ?=
PROCS ?=
KEEP ?=
# resource budget (bench/limits.sh): BENCH_CPUS / BENCH_MEM for the questdb container, set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 8g
BENCH_SPEC ?= questdb
export BENCH_CLIENT_CPUS ?= 6

benchmark:
	@docker compose ps --status running --services | grep -qx questdb || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=questdb-single-$$(date -u +%Y%m%dT%H%M%SZ) SMOKE=$(SMOKE) ROWS=$(ROWS) PROCS=$(PROCS) KEEP=$(KEEP) \
		HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose run --rm -T bench; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@$(EXEC) 'query=SELECT build()' | grep -oE 'QuestDB [0-9.]+'; echo
	docker compose --progress quiet run --rm -T psql \
		-c "SELECT table_name, partitionBy, walEnabled, dedup FROM tables() ORDER BY 1" \
		-c "SELECT name, writerTxn, sequencerTxn, suspended FROM wal_tables() ORDER BY 1"

cli:
	docker compose --progress quiet run --rm psql

down:
	docker compose --profile tools --profile bench down --volumes
