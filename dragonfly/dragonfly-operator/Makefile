CLUSTER_NAME := dragonfly-operator
NAMESPACE := dragonfly
OPERATOR_VERSION := v1.7.0
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
MASTER = $$($(KUBECTL) get pods -l app=dragonfly,role=master -o jsonpath='{.items[0].metadata.name}')
export CLUSTER_NAME NAMESPACE

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)
LUA_IMAGE := dragonfly-operator-lua-bench:local

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'
# a pod that is deleted when its command exits
RUN := $(KUBECTL) run --rm -i --quiet --restart=Never --pod-running-timeout=5m

.PHONY: up test persistence scale-up scale-down failover status bench bench-resp bench-lua cli down kind-up kind-down operator cluster

up: kind-up operator cluster

kind-up:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# kube-rbac-proxy only guards the operator's metrics endpoint
operator:
	$(HELM) upgrade --install dragonfly-operator oci://ghcr.io/dragonflydb/dragonfly-operator/helm/dragonfly-operator \
		--version $(OPERATOR_VERSION) --namespace dragonfly-operator-system --create-namespace \
		--set rbacProxy.enabled=false --wait --timeout 10m

cluster:
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f dragonfly.yaml
	./wait-ready.sh
	$(KUBECTL) get dragonfly,pods,pvc -L role

test:
	./wait-ready.sh 60
	./test.sh

persistence:
	./persistence.sh

# REPLICAS, not SIZE: nix dev shells export SIZE
scale-up scale-down:
	./scale.sh $(@:scale-%=%) $(REPLICAS)
	./test.sh

failover:
	./failover.sh

status:
	$(KUBECTL) get dragonfly,pods,pvc -L role
	$(KUBECTL) exec $(MASTER) -c dragonfly -- redis-cli info replication

bench: bench-resp bench-lua

# from a pod, against Service dragonfly (the master)
bench-resp:
	$(KUBECTL) delete pod bench-resp --ignore-not-found
	$(RUN) bench-resp --image=valkey/valkey:9.1.2-alpine --command -- \
		sh -c '$(call RESP_LOOP,-h dragonfly)' | $(RESP_TABLE)

bench-lua:
	docker build --quiet --tag $(LUA_IMAGE) bench
	kind load docker-image $(LUA_IMAGE) --name $(CLUSTER_NAME)
	$(KUBECTL) delete pod bench-lua --ignore-not-found
	$(RUN) bench-lua --image=$(LUA_IMAGE) --image-pull-policy=Never -- -addr dragonfly:6379 $(LUA_FLAGS)

cli:
	$(KUBECTL) exec -it $(MASTER) -c dragonfly -- redis-cli

down: kind-down

kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	docker image rm --force $(LUA_IMAGE) >/dev/null 2>&1 || true
