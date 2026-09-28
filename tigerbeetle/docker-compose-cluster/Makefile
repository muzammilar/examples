.PHONY: up test failover status cli down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

ADDRESSES := 10.203.53.10:3000,10.203.53.11:3000,10.203.53.12:3000
# repl on replica $(1); drop its connection log lines, give up after 60 s without a quorum
REPL = docker exec tigerbeetle-$(1) timeout 60 /tigerbeetle repl --cluster=0 --addresses=$(ADDRESSES) \
	--command='$(2)' 2>&1 | grep -v 'info(message_bus)'
BALANCES = $(call REPL,$(1),lookup_accounts id=1$(,) id=2$(,) id=3;) | grep -E '"(id|debits_posted|credits_posted)"' | paste - - -
, := ,
# The primary of view v is replica v mod 3; the highest view any replica has logged entering is current.
PRIMARY = $$(docker compose logs --no-log-prefix 2>/dev/null | grep -o 'transition_to_normal[a-z_]*: view=[0-9.]*' \
	| sed 's/.*[=.]//' | sort -n | tail -1 | awk '{print $$1 % 3}')

up:
	docker compose up --detach --wait
	$(MAKE) --no-print-directory status

# client/demo.py through the official Python client against all three replicas,
# then the balances through the built-in REPL (from tigerbeetle-0's container)
test:
	docker compose --progress quiet run --rm --build client
	@echo; echo "==== tigerbeetle repl: lookup_accounts id=1, id=2, id=3;"
	$(call BALANCES,0)

# stop the primary: the other two elect a new one (view change) and keep committing;
# restart it, then stop another replica to show the old primary caught up and votes again
failover:
	@p=$(PRIMARY); q=$$(( (p + 1) % 3 )); s=$$(( (p + 2) % 3 )); \
	echo "==> stopping the primary, tigerbeetle-$$p"; \
	docker compose stop tigerbeetle-$$p; \
	echo "==> transfer 501 via the repl in tigerbeetle-$$q (2 of 3 replicas are a quorum)"; \
	$(call REPL,$$q,create_transfers id=501 debit_account_id=1 credit_account_id=2 amount=1 ledger=1 code=10;); \
	$(call BALANCES,$$q); \
	echo "==> view change done, new primary: tigerbeetle-$(PRIMARY)"; \
	echo "==> starting tigerbeetle-$$p, then stopping tigerbeetle-$$s"; \
	docker compose up --detach --wait tigerbeetle-$$p; \
	docker compose stop tigerbeetle-$$s; \
	echo "==> transfer 502 via tigerbeetle-$$p: it rejoined, tigerbeetle-$$p + tigerbeetle-$$q are the quorum"; \
	$(call REPL,$$p,create_transfers id=502 debit_account_id=1 credit_account_id=2 amount=1 ledger=1 code=10;); \
	$(call BALANCES,$$p); \
	echo "==> starting tigerbeetle-$$s"; \
	docker compose up --detach --wait tigerbeetle-$$s
	$(MAKE) --no-print-directory status

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@echo "primary: tigerbeetle-$(PRIMARY)"
	@for r in 0 1 2; do docker logs tigerbeetle-$$r 2>&1 | grep transition_to_normal | tail -n 1 | sed "s/^/tigerbeetle-$$r: /"; done

cli:
	docker exec -it tigerbeetle-0 /tigerbeetle repl --cluster=0 --addresses=$(ADDRESSES)

down:
	docker compose --profile test down --volumes
