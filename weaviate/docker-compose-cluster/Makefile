.PHONY: up test failover status cli down

# iptables inside weaviate-3's network namespace (the tools image, with NET_ADMIN)
IPTABLES = docker run --rm --cap-add NET_ADMIN --network container:weaviate-3 weaviate-cluster-tools iptables

up:
	docker compose up --detach --wait
	curl -s localhost:18080/v1/nodes | jq -c '.nodes[] | {name, status, version}'

# 3 healthy nodes, collection with 3 shards x 3 replicas, QUORUM insert, placement, reads at ONE/QUORUM/ALL
test:
	docker compose run --rm tools sh scripts/test.sh

# 1. stop weaviate-3: QUORUM reads and writes keep working; start it, wait for 3 healthy nodes
#    and for it to catch up
# 2. partition weaviate-3 (reject its replica port 7101, it stays a cluster member): consistency
#    ALL fails, QUORUM works; heal it, ALL works again
failover:
	docker compose build --quiet tools
	@echo "==> stopping weaviate-3"
	docker compose stop weaviate-3
	docker compose run --rm tools sh scripts/failover.sh down
	@echo "==> starting weaviate-3"
	docker compose start --wait weaviate-3
	docker compose run --rm tools sh scripts/failover.sh up
	@echo "==> partitioning weaviate-3: iptables REJECT on its data port 7101"
	$(IPTABLES) -A INPUT -p tcp --dport 7101 -j REJECT --reject-with tcp-reset
	docker compose run --rm tools sh scripts/failover.sh partition || { $(IPTABLES) -F INPUT; exit 1; }
	@echo "==> healing the partition"
	$(IPTABLES) -F INPUT
	docker compose run --rm tools sh scripts/failover.sh heal

# node status, collections, and the live Landmark count (GraphQL Aggregate)
status:
	curl -s localhost:18080/v1/nodes | jq -c '.nodes[] | {name, status, version}'
	curl -s localhost:18080/v1/schema | jq -c '[.classes[] | {class, shards: .shardingConfig.desiredCount, replicas: .replicationConfig.factor}]'
	curl -s localhost:18080/v1/graphql -H 'Content-Type: application/json' \
	  -d '{"query": "{Aggregate{Landmark{meta{count}}}}"}' | jq -c '.errors // {Landmark: .data.Aggregate.Landmark[0].meta.count}'

cli:
	docker compose run --rm tools sh

down:
	docker compose --profile tools down --volumes --rmi local
