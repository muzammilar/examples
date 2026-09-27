.PHONY: up test status cli down

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

down:
	docker compose --profile tools down --volumes
