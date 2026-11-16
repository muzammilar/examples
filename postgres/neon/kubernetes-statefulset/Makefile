SHELL := /bin/sh
CLUSTER_NAME := neon-storage
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n neon-lab
ROWS ?= 1000000
export ROWS
SUPPORT := $(MAKE) -C dependencies COMPOSE_PROJECT_NAME=neon-kind-support GARAGE_CONFIG_DIR=$(CURDIR)/config/generated RESULTS_DIR=$(CURDIR)/results/support
RUN := $(KUBECTL) exec client -- sh /app/entrypoint.sh
.NOTPARALLEL:
.PHONY: kind-up up test benchmark branch status down kind-down failover disk-failure chaos-compute compute-replacement chaos-node test-production
kind-up:
	python3 lifecycle.py neon-kind-support neon-storage
	python3 prepare.py orders-workload
	python3 config.py
	$(SUPPORT) dependencies
	docker build -f Dockerfile.safekeeper -t neon-kind-safekeeper:dev .
	python3 import_images.py
	@kind get clusters | rg -qx '$(CLUSTER_NAME)' || kind create cluster --name $(CLUSTER_NAME) --image kindest/node:v1.34.0 --config kind-config.json
	@for node in $$(kind get nodes --name $(CLUSTER_NAME)); do docker network connect --gw-priority -1 neon-kind-support_default $$node || docker network inspect neon-kind-support_default --format '{{range .Containers}}{{println .Name}}{{end}}' | rg -qx "$$node"; done
	python3 load_images.py
up:
	python3 config.py objects
	$(KUBECTL) apply -f storage.json
	$(KUBECTL) rollout status statefulset/broker --timeout=180s
	$(KUBECTL) rollout status statefulset/pageserver --timeout=300s
	@for name in sk0 sk1 sk2; do $(KUBECTL) rollout status statefulset/$$name --timeout=180s || exit 1; done
	$(KUBECTL) wait --for=condition=Ready pod/client --timeout=180s
	$(RUN) init
	$(KUBECTL) apply -f compute.json
	$(KUBECTL) rollout status statefulset/compute --timeout=300s
	python3 wait_sql.py NEON_DSN
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
	python3 branch_readback.py
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==4 and all(v["samples"]==20 and v["errors"]==0 for v in d.values()), "incomplete benchmark results"'
branch:
	$(RUN) branch
	$(KUBECTL) apply -f branch.json
	$(KUBECTL) rollout status statefulset/branch --timeout=300s
	python3 wait_sql.py BRANCH_DSN
	$(RUN) verify-branch
failover:
	python3 fault.py wal-process
disk-failure:
	python3 fault.py wal-disk
chaos-compute:
	python3 fault.py compute-process
compute-replacement:
	python3 fault.py compute-cache
chaos-node:
	python3 fault.py node
test-production:
	$(MAKE) test
	$(MAKE) branch
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) chaos-node
	$(MAKE) chaos-compute
	$(MAKE) compute-replacement
	$(MAKE) test
status:
	$(KUBECTL) get pods,pvc,statefulsets
down:
	$(RUN) drop
	$(KUBECTL) delete namespace neon-lab --wait=true
kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	$(SUPPORT) docker-down
	docker image rm neon-kind-safekeeper:dev
	python3 import_images.py clean
	rm -f kind-config.json images.json storage.json compute.json branch.json results/kubeconfig
	rm -rf config/generated .build

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build

# Separate dependency/storage cases; the core five-case rerun stays independent.
.PHONY: pageserver-outage pageserver-cache chaos-s3 chaos-wal-quorum test-dependencies
pageserver-outage pageserver-cache:
	python3 dependency_fault.py $@
chaos-s3:
	python3 dependency_fault.py s3
chaos-wal-quorum:
	python3 dependency_fault.py wal-quorum
test-dependencies:
	$(MAKE) pageserver-outage
	$(MAKE) pageserver-cache
	$(MAKE) chaos-wal-quorum
	$(MAKE) chaos-s3
	$(MAKE) test

.PHONY: backup-restore
backup-restore:
	python3 dependency_fault.py backup
