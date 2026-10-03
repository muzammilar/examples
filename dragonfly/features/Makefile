.PHONY: up test memcached json search bloom cluster snapshot cli down

export DRAGONFLY_VERSION ?= v2.0.0
export VALKEY_VERSION ?= 9.1.2-alpine

TOOLS := docker compose --progress quiet run --rm -T tools

up:
	docker compose up --detach --wait
	$(TOOLS) valkey-cli -h dragonfly INFO server | grep -E '^(dragonfly_version|redis_mode|thread_count):'

test: memcached json search bloom cluster snapshot

# each feature is one script in scripts/, run in the tools container
memcached json search bloom cluster:
	@echo "-- $@"
	$(TOOLS) sh -e /scripts/$@.sh

# SAVE + BGSAVE, restart the container, the data is loaded back from /data
snapshot:
	@echo "-- $@"
	$(TOOLS) sh -e /scripts/snapshot-save.sh
	docker compose restart dragonfly
	docker compose up --detach --wait dragonfly
	$(TOOLS) sh -e /scripts/snapshot-check.sh

cli:
	docker compose --progress quiet run --rm tools valkey-cli -h dragonfly

down:
	docker compose --profile tools down --volumes
