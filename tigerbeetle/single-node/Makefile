.PHONY: up test status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

# built-in REPL client inside the replica's container, minus its connection log lines
REPL = docker exec tigerbeetle timeout 30 /tigerbeetle repl --cluster=0 --addresses=3000 \
	--command='$(1)' 2>&1 | grep -v 'info(message_bus)'

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix tigerbeetle | grep -E 'listening on|Allocated'

# client/demo.py through the official Python client (each step prints its title first),
# then one account through the built-in REPL
test:
	docker compose --progress quiet run --rm --build --no-deps client
	@echo; echo "==== tigerbeetle repl: lookup_accounts id=2;"
	$(call REPL,lookup_accounts id=2;)

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec tigerbeetle /tigerbeetle version
	$(call REPL,lookup_accounts id=1$(,) id=2$(,) id=3;) | grep -E '"(id|debits_posted|credits_posted)"' | paste - - -
, := ,

cli:
	docker exec -it tigerbeetle /tigerbeetle repl --cluster=0 --addresses=3000

down:
	docker compose --profile test down --volumes
