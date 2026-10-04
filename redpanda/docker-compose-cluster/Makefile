.PHONY: up test failover load scale-out scale-in scale-demo status cli down logs

SHELL := bash
.SHELLFLAGS := -o pipefail -c

up:
	docker compose up --detach --wait
	$(MAKE) --no-print-directory status

test:
	docker exec -i redpanda-cc-0 bash -s < scripts/01-replication.sh

# stop the leader of partition 0 under load, restart it, check no acknowledged record is lost
# (override RATE, DURATION, PARTITIONS)
failover:
	bash scripts/failover.sh

# 3 -> 5 brokers: start redpanda-3/4, wait until the balancer has moved replicas onto them
scale-out:
	bash scripts/scale.sh out

# 5 -> 3 brokers: decommission redpanda-3/4, wait until they left, remove them and their volumes
scale-in:
	bash scripts/scale.sh in

# scale-out then scale-in with the load client running; per-phase throughput, read-back check
# (RATE, RECORD_SIZE; BASELINE / SETTLE seconds)
scale-demo:
	bash scripts/scale-demo.sh

# the load client alone, in the foreground
load:
	docker compose --profile tools run --rm --build load

status:
	docker compose --profile scale ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'
	docker exec redpanda-cc-0 rpk cluster info -b
	docker exec redpanda-cc-0 rpk cluster health

cli:
	docker exec -it redpanda-cc-0 bash

logs:
	docker compose logs -f

down:
	docker rm -f redpanda-cc-load >/dev/null 2>&1 || true
	docker compose --profile scale --profile tools down --volumes --remove-orphans --rmi local
