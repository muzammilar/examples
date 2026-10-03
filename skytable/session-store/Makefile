.PHONY: up build run status cli down

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix skytable | grep -E 'Skytable v|listening on'

build:
	docker compose --profile run build app

# runs the app against the running server; it drops and recreates space `gateway`, so it can be
# repeated (override SESSIONS, LOAD_SINGLE, LOAD_CONNS, PIPE, SCALE_SECS, SCALE_CONNS,
# SCALE_DEPTHS, CLIENTS, REQUESTS, KEYS, LIMIT, INCREMENTS, NODELAY)
run:
	docker compose --progress quiet run --rm --build --no-deps app

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec skytable-session-store skyd --version
	docker exec skytable-session-store skysh -e 'inspect model gateway.sessions'

cli:
	docker exec -it skytable-session-store skysh

down:
	docker compose --profile run down --volumes --rmi local
