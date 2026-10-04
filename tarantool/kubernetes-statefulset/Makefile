.PHONY: up kind-cluster cluster test failover status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

CLUSTER_NAME := tarantool
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
PODS := tarantool-0 tarantool-1 tarantool-2
LUA = echo '$(2)' | $(KUBECTL) exec -i $(1) -- tt connect app:secret@127.0.0.1:3301 -f-
LEADER = $$(for p in $(PODS); do $(call LUA,$$p,return box.info.election.state) 2>/dev/null | \
	grep -q '^- leader' && echo $$p; done | head -n 1)
NOW = perl -MTime::HiRes=time -e 'printf "%.1f", time - ($$ARGV[0] // 0)'

up: kind-cluster cluster status

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

cluster:
	$(KUBECTL) create configmap tarantool-config --from-file=app/ --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) create configmap tarantool-scripts --from-file=scripts/ --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f tarantool.yaml
	$(KUBECTL) rollout status statefulset/tarantool --timeout=300s
	@for t in $$(seq 1 30); do [ -n "$(LEADER)" ] && break; sleep 1; done

# jobs/test.yaml: replication links, 1,000 sync writes, the same rows on every pod
test:
	@$(KUBECTL) delete job tarantool-test --ignore-not-found >/dev/null
	$(KUBECTL) apply -f jobs/test.yaml
	@$(KUBECTL) wait job/tarantool-test --for=condition=complete --timeout=120s & c=$$!; \
	$(KUBECTL) wait job/tarantool-test --for=condition=failed --timeout=120s >/dev/null 2>&1 & f=$$!; \
	wait -n $$c $$f; $(KUBECTL) logs job/tarantool-test; \
	[ "$$($(KUBECTL) get job tarantool-test -o jsonpath='{.status.succeeded}')" = 1 ]

# jobs/load.yaml writes for 40 s; after 10 s the leader pod is force-deleted; the StatefulSet
# recreates it with the same name and volume, and it rejoins as a follower
failover:
	@$(KUBECTL) delete job tarantool-load --ignore-not-found >/dev/null
	@$(KUBECTL) apply -f jobs/load.yaml >/dev/null; \
	$(KUBECTL) wait pod -l job-name=tarantool-load --for=jsonpath='{.status.phase}'=Running --timeout=60s >/dev/null; \
	sleep 10; old=$(LEADER); \
	echo "==> $$(date -u +%T) kubectl delete pod $$old --force (leader)"; \
	$(KUBECTL) delete pod $$old --force --grace-period=0 2>/dev/null; t0=$$($(NOW)); \
	new=""; while [ -z "$$new" ] || [ "$$new" = "$$old" ]; do sleep 0.2; new=$(LEADER); done; \
	echo "==> $$(date -u +%T) new leader: $$new, $$($(NOW) $$t0) s after the delete"; \
	$(KUBECTL) wait pod/$$old --for=condition=Ready --timeout=120s >/dev/null; \
	echo "==> $$(date -u +%T) $$old recreated and Ready, $$($(NOW) $$t0) s after the delete"; \
	$(KUBECTL) wait job/tarantool-load --for=condition=complete --timeout=120s >/dev/null; rc=$$?; \
	$(KUBECTL) logs job/tarantool-load | grep -vE 'errors=0$$' ; \
	$(MAKE) --no-print-directory status; exit $$rc

status:
	$(KUBECTL) get pods -o wide -l app=tarantool
	@for p in $(PODS); do $(call LUA,$$p,return cluster_status()) 2>/dev/null | sed -n 's/^- //p'; done

cli:
	$(KUBECTL) exec -it $(LEADER) -- tt connect app:secret@127.0.0.1:3301

logs:
	$(KUBECTL) logs -f -l app=tarantool --prefix

down:
	kind delete cluster --name $(CLUSTER_NAME)
