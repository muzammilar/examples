.PHONY: up test status cli down

PD_CTL := docker exec tikv-pd0 /pd-ctl -u http://pd0:2379
# pd-ctl prints JSON; jq (inside the PD image) turns it into one line per store / region
PD_JQ := docker exec -i tikv-pd0 jq -r

up:
	docker compose up --detach --wait
	docker compose build client
	@echo "==> waiting until PD sees 3 stores Up (first heartbeat after ~10 s) and every region has 3 replicas"
	for i in $$(seq 90); do [ "$$($(PD_CTL) store | $(PD_JQ) '[.stores[] | select(.store.state_name == "Up")] | length')" = 3 ] \
	  && [ "$$($(PD_CTL) region | $(PD_JQ) '[.regions[] | select((.peers | length) < 3)] | length')" = 0 ] && break; sleep 2; done
	[ "$$($(PD_CTL) region | $(PD_JQ) '[.regions[] | select((.peers | length) < 3)] | length')" = 0 ]
	$(PD_CTL) store | $(PD_JQ) '.stores[] | "store \(.store.id) \(.store.address) \(.store.state_name)"'

# client/main.go: RawKV put/get/scan/delete, TxnKV commit + optimistic and pessimistic conflicts
test:
	docker compose run --rm client

status:
	$(PD_CTL) member | $(PD_JQ) '"PD leader: \(.leader.name)", (.members[] | "member \(.name) \(.client_urls[0])")'
	$(PD_CTL) store | $(PD_JQ) '.stores[] | "store \(.store.id) \(.store.address) \(.store.state_name) version=\(.store.version) leaders=\(.status.leader_count) regions=\(.status.region_count) capacity=\(.status.capacity) available=\(.status.available)"'
	$(PD_CTL) region | $(PD_JQ) '"regions: \(.count)", (.regions[] | "region \(.id) [\(.start_key), \(.end_key)) leader=store \(.leader.store_id) peers=\([.peers[].store_id] | map(tostring) | join(","))")'
	$(PD_CTL) config show replication | $(PD_JQ) '"max-replicas: \(."max-replicas")"'

# interactive pd-ctl (try: store, region, region key --format=raw raw/user/001, member, config show)
cli:
	docker exec -it tikv-pd0 /pd-ctl -u http://pd0:2379 -i

down:
	docker compose down --volumes
