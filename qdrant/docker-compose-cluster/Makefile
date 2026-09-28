.PHONY: up test failover status cli down

up:
	docker compose up --detach --wait
	curl -s localhost:16333/cluster | jq -c '.result | {peers: ([.peers[].uri] | sort), leader: .peers[.raft_info.leader | tostring].uri}'

# cluster members + Raft leader, collection with 3 shards x 2 replicas, upsert, placement, search
test:
	docker compose run --rm tools sh scripts/test.sh

# stop qdrant-3: search, count and upserts keep working from the other replica of each shard,
# the cluster reports the unreachable peer and marks its replicas Dead; then start it again and
# wait until all replicas are Active
failover:
	@echo "==> stopping qdrant-3"
	docker compose stop qdrant-3
	docker compose run --rm tools sh scripts/failover.sh down
	@echo "==> starting qdrant-3"
	docker compose start --wait qdrant-3
	docker compose run --rm tools sh scripts/failover.sh up

status:
	@for p in 16333 16343 16353; do \
	  curl -s localhost:$$p/cluster | jq -c '.result | {peer: .peers[.peer_id | tostring].uri, role: .raft_info.role, term: .raft_info.term, commit: .raft_info.commit}' \
	    || echo "localhost:$$p unreachable"; \
	done
	curl -s localhost:16333/collections | jq -c '.result.collections'

cli:
	docker compose run --rm tools sh

down:
	docker compose --profile tools down --volumes --rmi local
