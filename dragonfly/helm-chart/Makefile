CLUSTER ?= dragonfly-helm
NAMESPACE ?= dragonfly
CHART_VERSION ?= v2.0.0
export CLUSTER NAMESPACE

KUBECTL := kubectl --context kind-$(CLUSTER) -n $(NAMESPACE)

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)
LUA_IMAGE := dragonfly-helm-lua-bench:local

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'
# a pod that is deleted when its command exits
RUN := $(KUBECTL) run --rm -i --quiet --restart=Never --pod-running-timeout=5m

.PHONY: up test failover status bench bench-resp bench-lua cli down

up:
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --name $(CLUSTER) --wait 2m
	helm --kube-context kind-$(CLUSTER) upgrade --install dragonfly oci://ghcr.io/dragonflydb/dragonfly/helm/dragonfly \
		--version $(CHART_VERSION) -n $(NAMESPACE) --create-namespace -f values.yaml --wait --timeout 10m
	./dragonfly.sh wait
	$(KUBECTL) get pods,svc,pvc

test:
	./dragonfly.sh test

# delete dragonfly-0: no replica is promoted, it comes back from its snapshot
failover:
	./dragonfly.sh failover

status:
	./dragonfly.sh status

bench: bench-resp bench-lua

# from a pod, against Service dragonfly-primary (dragonfly-0)
bench-resp:
	$(KUBECTL) delete pod bench-resp --ignore-not-found
	$(RUN) bench-resp --image=valkey/valkey:9.1.2-alpine --command -- \
		sh -c '$(call RESP_LOOP,-h dragonfly-primary)' | $(RESP_TABLE)

bench-lua:
	docker build --quiet --tag $(LUA_IMAGE) bench
	kind load docker-image $(LUA_IMAGE) --name $(CLUSTER)
	$(KUBECTL) delete pod bench-lua --ignore-not-found
	$(RUN) bench-lua --image=$(LUA_IMAGE) --image-pull-policy=Never -- -addr dragonfly-primary:6379 $(LUA_FLAGS)

cli:
	$(KUBECTL) exec -it dragonfly-0 -- redis-cli

down:
	kind delete cluster --name $(CLUSTER)
	docker image rm --force $(LUA_IMAGE) >/dev/null 2>&1 || true
