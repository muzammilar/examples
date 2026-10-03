.PHONY: up test benchmark status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

SKYSH = docker exec skytable skysh

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix skytable | grep -E 'Skytable v|listening on'

# blueql/1-root.blueql as root, blueql/2-app-user.blueql as the standard user it creates (dropped
# again at the end), then a restart: the rows are read back from disk
test:
	docker exec skytable sh /blueql/run.sh /blueql/1-root.blueql
	docker exec skytable sh /blueql/run.sh /blueql/2-app-user.blueql --user app --password app-password-1234
	@echo; echo "==== drop user app, restart skytable, read alice back"
	$(SKYSH) -e 'sysctl drop user app'
	docker compose restart skytable && docker compose up --detach --wait skytable 2>/dev/null
	$(SKYSH) -e 'select username, followers, tags from demo.users where username = "alice"'

# sky-bench (uniform_std_v1) against the running server: ROWS unique keys (SMOKE=1: 100k), each
# INSERTed, UPDATEd, SELECTed and DELETEd once from CONNECTIONS connections on THREADS threads;
# prints a summary table, keeps results/skytable-single-<UTC time>.{txt,log,json}
SMOKE ?=
ROWS ?= $(if $(filter 1,$(SMOKE)),100000,1000000)
THREADS ?= 4
CONNECTIONS ?= 32

# resource budget for `make benchmark` (bench/limits.sh): BENCH_CPUS / BENCH_MEM in total for the
# database containers in BENCH_SPEC (equal shares; service=CPUS:MEM is a fixed amount), set with
# `docker update` for the run and restored afterwards; the bench client has BENCH_CLIENT_CPUS
BENCH_CPUS ?= 4
BENCH_MEM ?= 4g
BENCH_SPEC ?= skytable
export BENCH_CLIENT_CPUS ?= 4

benchmark:
	@docker compose ps --status running --services | grep -qx skytable || { echo 'run `make up` first'; exit 1; }
	@mkdir -p results
	@BENCH_LIMITS=$$(sh bench/limits.sh apply $(BENCH_CPUS) $(BENCH_MEM) $(BENCH_SPEC)) || { sh bench/limits.sh restore; exit 1; }; \
	export BENCH_LIMITS NAME=skytable-single-$$(date -u +%Y%m%dT%H%M%SZ) ROWS=$(ROWS) THREADS=$(THREADS) \
		CONNECTIONS=$(CONNECTIONS) HOST_INFO="$$(uname -sm)" \
		DOCKER_INFO="$$(docker info --format 'Docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes')"; \
	docker compose --progress quiet run --rm -T --build --no-deps bench && docker compose run --rm -T bench-report; \
	rc=$$?; sh bench/limits.sh restore; exit $$rc

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec skytable skyd --version
	$(SKYSH) -e 'inspect global'

cli:
	docker exec -it skytable skysh

down:
	docker compose --profile bench down --volumes --rmi local
