CLUSTER_NAME := valkey-ot-operator
NAMESPACE := valkey
OPERATOR_VERSION := 0.26.1
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
export CLUSTER_NAME NAMESPACE

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)
LUA_IMAGE := valkey-ot-operator-lua-bench:local

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'
# a pod that is deleted when its command exits
RUN := $(KUBECTL) run --rm -i --quiet --restart=Never --pod-running-timeout=5m

.PHONY: up test scale-up scale-down failover status bench bench-resp bench-lua cli down kind-up kind-down operator cluster

up: kind-up operator cluster

kind-up:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# the init container writes redis.conf, so the plain valkey image works
operator:
	helm repo add ot-helm https://ot-container-kit.github.io/helm-charts/ >/dev/null 2>&1 || true
	helm repo update ot-helm
	$(HELM) upgrade --install redis-operator ot-helm/redis-operator \
		--version $(OPERATOR_VERSION) --namespace ot-operators --create-namespace \
		--set featureGates.GenerateConfigInInitContainer=true --wait --timeout 10m

cluster:
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f rediscluster.yaml
	$(KUBECTL) wait rediscluster/valkey --for=jsonpath='{.status.state}'=Ready --timeout=10m
	$(KUBECTL) get rediscluster,pods

test:
	$(KUBECTL) exec -i valkey-leader-0 -c valkey-leader -- sh -es \
		$$($(KUBECTL) get rediscluster valkey -o jsonpath='{.spec.clusterSize}') < test.sh

# PRIMARIES, not SIZE: nix dev shells export SIZE
scale-up scale-down:
	./scale.sh $(@:scale-%=%) $(PRIMARIES)
	@$(MAKE) --no-print-directory test

failover:
	HARD=$(HARD) ./failover.sh

status:
	$(KUBECTL) get rediscluster,pods -o wide
	$(KUBECTL) exec valkey-leader-0 -c valkey-leader -- valkey-cli cluster nodes | sort -k3

bench: bench-resp bench-lua

# from a pod, through Service valkey-leader; both clients find the primaries
# with CLUSTER NODES / CLUSTER SHARDS and talk to the pods directly
bench-resp:
	$(KUBECTL) delete pod bench-resp --ignore-not-found
	$(RUN) bench-resp --image=valkey/valkey:9.1.2-alpine --command -- \
		sh -c '$(call RESP_LOOP,-h valkey-leader --cluster)' | $(RESP_TABLE)

bench-lua:
	docker build --quiet --tag $(LUA_IMAGE) bench
	kind load docker-image $(LUA_IMAGE) --name $(CLUSTER_NAME)
	$(KUBECTL) delete pod bench-lua --ignore-not-found
	$(RUN) bench-lua --image=$(LUA_IMAGE) --image-pull-policy=Never -- -addr valkey-leader:6379 $(LUA_FLAGS)

cli:
	$(KUBECTL) exec -it valkey-leader-0 -c valkey-leader -- valkey-cli -c

down: kind-down

kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	docker image rm --force $(LUA_IMAGE) >/dev/null 2>&1 || true
