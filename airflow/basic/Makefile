.PHONY: up lint test e2e status cli flower down

up:
	docker compose up --detach --wait

# ruff lint + format check on dags/ and tests/
lint:
	uv run ruff check .
	uv run ruff format --check .

# unit tests: DAGs import cleanly, structure, task logic (no containers needed)
test:
	uv run pytest

# end to end: trigger dags/etl_example.py on the running stack and wait for the run to succeed
e2e:
	./scripts/e2e.sh

status:
	curl -s localhost:8080/api/v2/monitor/health | jq -c
	docker compose exec -T airflow-scheduler airflow dags list

cli:
	docker compose run --rm airflow-cli bash

flower:
	docker compose --profile flower up --detach --wait flower

down:
	docker compose --profile debug --profile flower down --volumes --remove-orphans
