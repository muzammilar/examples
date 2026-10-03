.PHONY: up test failover benchmark status cli down kind-cluster image cluster

SHELL := bash
.SHELLFLAGS := -o pipefail -ec

CLUSTER_NAME := tigerbeetle
NAMESPACE := tigerbeetle
TIGERBEETLE_VERSION := 0.17.9
CLIENT_IMAGE := tigerbeetle-client:$(TIGERBEETLE_VERSION)
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
ADDRESSES := 10.96.53.10:3000,10.96.53.11:3000,10.96.53.12:3000
# the primary of view v is replica v mod 3; the highest view any replica has logged entering is current
PRIMARY = $$(for r in 0 1 2; do $(KUBECTL) logs tigerbeetle-$$r 2>/dev/null; done \
	| grep -o 'transition_to_normal[a-z_]*: view=[0-9.]*' | sed 's/.*[=.]//' | sort -n | tail -1 | awk '{print $$1 % 3}')
# run a Job from jobs/$(1).yaml, overriding its `{ name: KEY, value: "..." }` env entries with
# $(2) (KEY=VALUE ...)
JOB = $(KUBECTL) delete job $(1) --ignore-not-found >/dev/null; \
	sed -E -e '' $(foreach kv,$(2),-e 's/\{ name: $(word 1,$(subst =, ,$(kv))), value: "[^"]*" \}/{ name: $(word 1,$(subst =, ,$(kv))), value: "$(word 2,$(subst =, ,$(kv)))" }/') \
		jobs/$(1).yaml | $(KUBECTL) apply -f - >/dev/null
# wait for Job $(1) to finish, print its log, fail if it failed
JOB_WAIT = $(KUBECTL) get job $(1) >/dev/null; until [ -n "$$($(KUBECTL) get job $(1) -o jsonpath='{.status.succeeded}{.status.failed}')" ]; do sleep 2; done; \
	$(KUBECTL) logs job/$(1); [ "$$($(KUBECTL) get job $(1) -o jsonpath='{.status.succeeded}')" = 1 ]
UID_OF = $$($(KUBECTL) get pod $(1) -o jsonpath='{.metadata.uid}' 2>/dev/null)
# block until pod $(1) is a different pod than uid $(2) and ready
WAIT_NEW_POD = until u=$(call UID_OF,$(1)); [ -n "$$u" ] && [ "$$u" != "$(2)" ]; do sleep 1; done; \
	$(KUBECTL) wait pod/$(1) --for=condition=Ready --timeout=300s >/dev/null

up: kind-cluster image cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

# the Python test client (client/), loaded into the kind nodes (the Jobs use imagePullPolicy: Never)
image:
	docker build -q --build-arg TIGERBEETLE_VERSION=$(TIGERBEETLE_VERSION) -t $(CLIENT_IMAGE) client
	kind load docker-image --name $(CLUSTER_NAME) $(CLIENT_IMAGE)

# all three pods start together (podManagementPolicy: Parallel) and format; once all are ready
# BOOTSTRAPPED=true makes any later pod without a data file run `tigerbeetle recover` instead
cluster:
	$(KUBECTL) apply -f tigerbeetle.yaml
	$(KUBECTL) rollout status statefulset/tigerbeetle --timeout=10m
	$(KUBECTL) patch configmap tigerbeetle --type merge -p '{"data":{"BOOTSTRAPPED":"true"}}'
	$(MAKE) --no-print-directory status

test:
	@echo "==== Job demo: client/demo.py against $(ADDRESSES)"
	@$(call JOB,demo,)
	@$(call JOB_WAIT,demo)
	@echo; echo "==== tigerbeetle repl in tigerbeetle-0: lookup_accounts id=1, id=2, id=3;"
	$(KUBECTL) exec tigerbeetle-0 -- timeout 60 /tigerbeetle repl --cluster=0 --addresses=$(ADDRESSES) \
		--command='lookup_accounts id=1, id=2, id=3;' 2>&1 | grep -E '"(id|debits_posted|credits_posted)"' | paste - - -

# kill the primary's pod (no grace period) under load: the other two elect a new primary,
# the StatefulSet recreates the pod on the same PVC and it catches up. DURATION, KILL_AFTER in s.
DURATION ?= 40
KILL_AFTER ?= 10
failover:
	@p=$(PRIMARY); echo "==> primary: tigerbeetle-$$p; starting Job load ($(DURATION) s)"; \
	$(call JOB,load,DURATION=$(DURATION)); \
	$(KUBECTL) wait pod -l job-name=load --for=condition=Ready --timeout=120s >/dev/null; \
	sleep $(KILL_AFTER); t0=$$(date +%s); uid=$(call UID_OF,tigerbeetle-$$p); \
	echo "==> $$(date -u +%T) killing pod tigerbeetle-$$p (--grace-period=0 --force)"; \
	$(KUBECTL) delete pod tigerbeetle-$$p --grace-period=0 --force 2>/dev/null; \
	$(call WAIT_NEW_POD,tigerbeetle-$$p,$$uid); \
	echo "==> $$(date -u +%T) tigerbeetle-$$p is back and ready after $$(( $$(date +%s) - t0 )) s; primary now: tigerbeetle-$(PRIMARY)"; \
	$(call JOB_WAIT,load)
	$(MAKE) --no-print-directory status

# `tigerbeetle benchmark` as a Job (client capped at 2 CPUs, replicas at 2 CPUs / 4 GiB each):
# TRANSFERS transfers (SMOKE=1: 100k) between ACCOUNTS accounts from CLIENTS clients, batches
# of up to BATCH; the full output goes to results/tigerbeetle-kubernetes-<UTC time>.txt
SMOKE ?=
TRANSFERS ?= $(if $(filter 1,$(SMOKE)),100000,1000000)
ACCOUNTS ?= 10000
CLIENTS ?= 1
BATCH ?= 8189
benchmark:
	@mkdir -p results; out=results/tigerbeetle-kubernetes-$$(date -u +%Y%m%dT%H%M%SZ).txt; \
	$(call JOB,bench,TRANSFERS=$(TRANSFERS) ACCOUNTS=$(ACCOUNTS) CLIENTS=$(CLIENTS) BATCH=$(BATCH)); \
	{ $(call JOB_WAIT,bench); } > $$out 2>&1 || { cat $$out; exit 1; }; \
	grep -vE '^(info|warning|debug)|^[0-9]{4}-' $$out; echo "(full output: $$out)"

status:
	$(KUBECTL) get pods -o wide -l app=tigerbeetle
	@echo "primary: tigerbeetle-$(PRIMARY)"
	@for r in 0 1 2; do $(KUBECTL) logs tigerbeetle-$$r 2>/dev/null | grep transition_to_normal | tail -n 1 | sed "s/^/tigerbeetle-$$r: /"; done

cli:
	$(KUBECTL) exec -it tigerbeetle-0 -- /tigerbeetle repl --cluster=0 --addresses=$(ADDRESSES)

down:
	kind delete cluster --name $(CLUSTER_NAME)
