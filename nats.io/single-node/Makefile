.PHONY: up test app status cli down

up:
	docker compose up --detach --wait

# core pub/sub + request/reply, a JetStream stream + pull consumer, a KV bucket
test:
	for f in scripts/*.sh; do echo "-- $$f"; docker compose --progress quiet run --rm -T tools sh -ex /$$f 2>&1 || exit 1; done

# the same concepts from Python (nats-py, asyncio); image is (re)built from app/
app:
	docker compose --progress quiet --profile tools build app
	for f in app/*.py; do echo "-- $$f"; docker compose --progress quiet run --rm -T app python $$(basename $$f) 2>&1 || exit 1; done

status:
	docker compose --progress quiet run --rm -T tools nats server check jetstream

cli:
	docker compose --progress quiet run --rm tools

down:
	docker compose --profile tools down --volumes --rmi local
