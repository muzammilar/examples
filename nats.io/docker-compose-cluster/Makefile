.PHONY: up test failover status cli down

TOOLS := docker compose --progress quiet run --rm -T tools
SYS := --user sys --password sys

up:
	docker compose up --detach --wait
	$(TOOLS) nats $(SYS) server list 3

# cluster membership + meta leader, core pub/sub across servers, an R3 stream + consumer, an R3 KV bucket
test:
	for f in scripts/*.sh; do echo "-- $$f"; $(TOOLS) sh -e /$$f 2>&1 || exit 1; done

# stop the leader of an R3 stream: a new leader is elected, publish + consume go on;
# then start it again and wait until its replica is current
failover:
	@echo "==> R3 stream EVENTS: 5 messages published and consumed"
	$(TOOLS) sh -e /failover/setup.sh
	@set -e; leader=$$($(TOOLS) sh -c 'nats stream info EVENTS --json | jq -r .cluster.leader'); \
	echo "==> stopping the stream leader $$leader"; \
	docker compose stop $$leader; \
	echo "==> with $$leader down: new leader, 5 more messages published and consumed"; \
	$(TOOLS) sh -e /failover/during.sh $$leader; \
	echo "==> starting $$leader"; \
	docker compose up --detach --wait $$leader; \
	echo "==> $$leader catches up"; \
	$(TOOLS) sh -e /failover/after.sh $$leader

status:
	$(TOOLS) nats $(SYS) server report jetstream
	$(TOOLS) nats stream report

cli:
	docker compose --progress quiet run --rm tools

down:
	docker compose --profile tools down --volumes
