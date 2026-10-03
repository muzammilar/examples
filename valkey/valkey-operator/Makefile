CLUSTER_NAME := valkey-operator
NAMESPACE := valkey
CHART_VERSION := 0.7.0
OPERATOR_VERSION := v0.7.1
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
# the server container sets VALKEYCLI_AUTH to the _operator password without a user;
# drop it and connect as the default user
CLI := $(KUBECTL) exec valkey-valkey-0-0-0 -c server -- env -u VALKEYCLI_AUTH valkey-cli
SPEC = $$($(KUBECTL) get valkeycluster valkey -o jsonpath='{.spec.$(1)}')

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)
LUA_IMAGE := valkey-operator-lua-bench:local

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'
# a pod that is deleted when its command exits
RUN := $(KUBECTL) run --rm -i --quiet --restart=Never --pod-running-timeout=5m

.PHONY: up test persistence scale scale-up scale-down failover status bench bench-resp bench-lua cli down kind-up kind-down operator cluster

up: kind-up operator cluster

kind-up:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

operator:
	$(HELM) upgrade --install valkey-operator valkey-operator --repo https://valkey.io/valkey-helm \
		--version $(CHART_VERSION) --set image.tag=$(OPERATOR_VERSION) \
		--namespace valkey-operator-system --create-namespace --wait --timeout 10m

cluster:
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f valkeycluster.yaml
	./wait-healthy.sh
	$(KUBECTL) get valkeycluster,valkeynodes,pods,pvc

test:
	./wait-healthy.sh 60
	$(CLI) cluster info | grep -E '^cluster_(state|slots_ok|known_nodes|size):'
	$(CLI) --cluster check 127.0.0.1:6379
	./keys.sh write 100
	./keys.sh check 100

persistence:
	@echo "removing spec.persistence must be rejected:"
	! $(KUBECTL) patch valkeycluster valkey --dry-run=server --type json -p '[{"op":"remove","path":"/spec/persistence"}]'
	./keys.sh write 1000
	$(KUBECTL) delete pods -l valkey.io/cluster=valkey
	./wait-healthy.sh
	./keys.sh check 1000

scale:
	./keys.sh write 1000
	$(KUBECTL) patch valkeycluster valkey --type merge -p \
		"{\"spec\":{\"shards\":$(or $(SHARDS),$(call SPEC,shards)),\"replicas\":$(or $(REPLICAS),$(call SPEC,replicas))}}"
	./wait-healthy.sh 900
	./keys.sh check 1000
	$(CLI) --cluster info 127.0.0.1:6379

scale-up:
	$(MAKE) --no-print-directory scale SHARDS=$(or $(SHARDS),$$(($(call SPEC,shards) + 1)))

scale-down:
	$(MAKE) --no-print-directory scale SHARDS=$(or $(SHARDS),$$(($(call SPEC,shards) - 1)))

failover:
	./failover.sh

status:
	$(KUBECTL) get valkeycluster,valkeynodes,pods,pvc
	$(CLI) --cluster info 127.0.0.1:6379

bench: bench-resp bench-lua

# from a pod, through the headless Service valkey-valkey; both clients find the
# primaries with CLUSTER NODES / CLUSTER SHARDS and talk to the pods directly
bench-resp:
	$(KUBECTL) delete pod bench-resp --ignore-not-found
	$(RUN) bench-resp --image=valkey/valkey:9.1.2-alpine --command -- \
		sh -c '$(call RESP_LOOP,-h valkey-valkey --cluster)' | $(RESP_TABLE)

bench-lua:
	docker build --quiet --tag $(LUA_IMAGE) bench
	kind load docker-image $(LUA_IMAGE) --name $(CLUSTER_NAME)
	$(KUBECTL) delete pod bench-lua --ignore-not-found
	$(RUN) bench-lua --image=$(LUA_IMAGE) --image-pull-policy=Never -- -addr valkey-valkey:6379 $(LUA_FLAGS)

cli:
	$(KUBECTL) exec -it valkey-valkey-0-0-0 -c server -- env -u VALKEYCLI_AUTH valkey-cli -c

down: kind-down

kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	docker image rm --force $(LUA_IMAGE) >/dev/null 2>&1 || true
