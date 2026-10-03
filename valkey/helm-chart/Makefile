CLUSTER ?= valkey-helm
NAMESPACE ?= valkey
CHART_VERSION ?= 0.12.0
VALKEY_VERSION ?= 9.1.2
export CLUSTER NAMESPACE VALKEY_VERSION

KUBECTL := kubectl --context kind-$(CLUSTER) -n $(NAMESPACE)

BENCH_N ?= 200000
BENCH_C ?= 50
BENCH_KEYS ?= 100000
LUA_FLAGS = -n $(BENCH_N) -c $(BENCH_C) -keys $(BENCH_KEYS)
LUA_IMAGE := valkey-helm-lua-bench:local

# valkey-benchmark at pipeline 1 and 16; RESP_TABLE turns its CSV into op, ops/s, p50, p99
RESP_LOOP = for p in 1 16; do echo "pipeline $$p"; valkey-benchmark $(1) \
	-t set,get,incr,lpush,hset -n $(BENCH_N) -c $(BENCH_C) -r $(BENCH_KEYS) -P $$p -q --csv; done
RESP_TABLE = tr -d '"\r' | awk -F, '/^pipeline/ { printf "%s\n%-8s %9s %7s %7s\n", $$0, "op", "ops/s", "p50 ms", "p99 ms"; next } \
	$$1 != "test" && NF > 6 { n++; printf "%-8s %9.0f %7.2f %7.2f\n", $$1, $$2, $$5, $$7 } END { exit !n }'
# a pod that is deleted when its command exits
RUN := $(KUBECTL) run --rm -i --quiet --restart=Never --pod-running-timeout=5m

.PHONY: up test failover status bench bench-resp bench-lua down kind-up kind-test kind-failover kind-status kind-bench kind-bench-resp kind-bench-lua kind-cli kind-down compose-up compose-test compose-failover compose-status compose-bench compose-bench-resp compose-bench-lua compose-cli compose-down

up: kind-up compose-up
test: kind-test compose-test
failover: kind-failover compose-failover
status: kind-status compose-status
bench: kind-bench compose-bench
bench-resp: kind-bench-resp compose-bench-resp
bench-lua: kind-bench-lua compose-bench-lua
down: kind-down compose-down

kind-up:
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --name $(CLUSTER) --wait 2m
	helm --kube-context kind-$(CLUSTER) upgrade --install valkey valkey --repo https://valkey.io/valkey-helm/ \
		--version $(CHART_VERSION) -n $(NAMESPACE) --create-namespace \
		-f values.yaml --set image.tag=$(VALKEY_VERSION) --wait --timeout 10m
	$(KUBECTL) get pods,svc

kind-test:
	./kind.sh test

# delete valkey-0: nothing gets promoted, the StatefulSet brings it back on its PVC
kind-failover:
	./kind.sh failover

kind-status:
	./kind.sh status

kind-bench: kind-bench-resp kind-bench-lua

# from a pod, against Service valkey (valkey-0, the primary)
kind-bench-resp:
	$(KUBECTL) delete pod bench-resp --ignore-not-found
	$(RUN) bench-resp --image=valkey/valkey:$(VALKEY_VERSION)-alpine --command -- \
		sh -c '$(call RESP_LOOP,-h valkey)' | $(RESP_TABLE)

kind-bench-lua:
	docker build --quiet --tag $(LUA_IMAGE) bench
	kind load docker-image $(LUA_IMAGE) --name $(CLUSTER)
	$(KUBECTL) delete pod bench-lua --ignore-not-found
	$(RUN) bench-lua --image=$(LUA_IMAGE) --image-pull-policy=Never -- -addr valkey:6379 $(LUA_FLAGS)

kind-cli:
	$(KUBECTL) exec -it valkey-0 -c valkey -- valkey-cli

kind-down:
	kind delete cluster --name $(CLUSTER)
	docker image rm --force $(LUA_IMAGE) >/dev/null 2>&1 || true

compose-up:
	docker compose up --detach --wait
	docker compose run --rm cluster-init

compose-test:
	./compose.sh test

# stop the primary of slot 0: its replica takes over; start it again, it rejoins as a replica
compose-failover:
	./compose.sh failover

compose-status:
	./compose.sh status

compose-bench: compose-bench-resp compose-bench-lua

compose-bench-resp:
	docker compose --progress quiet --profile bench run --rm -T resp \
		sh -c '$(call RESP_LOOP,-h valkey-1 --cluster)' | $(RESP_TABLE)

compose-bench-lua:
	docker compose --progress quiet --profile bench run --build --rm lua -addr valkey-1:6379 $(LUA_FLAGS)

compose-cli:
	docker compose exec valkey-1 valkey-cli -c

compose-down:
	docker compose --profile init --profile bench down --volumes --rmi local
