.PHONY: up build run status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

up:
	docker compose up --detach --wait
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'

build:
	docker compose --profile run build client

# client/main.go: three phases on the same 200k transfers, audit after each; exit 1 on a
# broken invariant. Output also in results/wallet-<UTC time>.txt
run: build
	@mkdir -p results
	docker compose --progress quiet run --rm -T client | tee results/wallet-$$(date -u +%Y%m%dT%H%M%SZ).txt

status:
	docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	@echo 'return audit()' | docker exec -i tarantool-wallet tt connect app:secret@127.0.0.1:3301 -f-

cli:
	docker exec -it tarantool-wallet tt connect app:secret@127.0.0.1:3301

logs:
	docker compose logs --follow

down:
	docker compose --profile run down --volumes --remove-orphans --rmi local
