.PHONY: up build run status down

up:
	docker compose up --detach --wait
	docker compose logs --no-log-prefix tigerbeetle | grep -E 'listening on|Allocated'

# builds the official Rust client from source at the server's tag (first run: a few minutes)
build:
	docker compose --profile run build showcase

# the showcase against the running replica; every run uses fresh accounts, so it can be repeated
# (override USERS, MERCHANTS, PAYMENTS, CLIENTS, HOLDS, EXCHANGES)
run:
	docker compose --progress quiet run --rm --build --no-deps showcase

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec tigerbeetle-ledger /tigerbeetle version

down:
	docker compose --profile run down --volumes --rmi local
