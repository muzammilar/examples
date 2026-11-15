SHELL := /bin/sh
CLUSTER_NAME := greptime-operator
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
NAMESPACE := greptime-lab
SUPPORT_PROJECT := greptime-kind-support
SUPPORT := $(MAKE) -C dependencies COMPOSE_PROJECT_NAME=$(SUPPORT_PROJECT) GARAGE_CONFIG_DIR=$(CURDIR)/config/generated RESULTS_DIR=$(CURDIR)/results/support
SERIES ?= 10000
POINTS ?= 100
LOGS ?= 1000000
export SERIES POINTS LOGS
RUN := $(KUBECTL) -n $(NAMESPACE) exec client -- client
.NOTPARALLEL:
.PHONY: kind-up up test benchmark status cli down kind-down scale-out scale-in failover disk-failure scale-up scale-down rolling-restart chaos-node chaos-operator chaos-wal-quorum test-production
kind-up:
	python3 lifecycle.py greptime-kind-support greptime-operator
	python3 prepare.py telemetry-workload
	python3 config.py
	$(SUPPORT) dependencies
	@kind get clusters | rg -qx '$(CLUSTER_NAME)' || kind create cluster --name $(CLUSTER_NAME) --image kindest/node:v1.34.0 --config kind-config.json
	@for node in $$(kind get nodes --name $(CLUSTER_NAME)); do docker network connect $(SUPPORT_PROJECT)_default $$node || docker network inspect $(SUPPORT_PROJECT)_default --format '{{range .Containers}}{{println .Name}}{{end}}' | rg -qx "$$node"; done
	python3 load_image.py
up:
	python3 config.py endpoints
	$(HELM) upgrade --install greptime-operator https://github.com/GreptimeTeam/helm-charts/releases/download/greptimedb-operator-1.3.0/greptimedb-operator-1.3.0.tgz --namespace $(NAMESPACE) --values operator-values.yaml --wait --timeout 5m
	$(HELM) upgrade --install telemetry https://github.com/GreptimeTeam/helm-charts/releases/download/greptimedb-cluster-1.3.3/greptimedb-cluster-1.3.3.tgz --namespace $(NAMESPACE) --values values.yaml --wait --timeout 10m
	$(KUBECTL) -n $(NAMESPACE) wait --for=condition=Ready greptimedbcluster/telemetry --timeout=600s
	$(KUBECTL) apply -f client.json
	$(KUBECTL) -n $(NAMESPACE) wait --for=condition=Ready pod/client --timeout=120s
	$(RUN) seed
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==6 and all(v["requests"]==20 and v["request_errors"]==0 for v in d.values()), "incomplete benchmark results"'
scale-out:
	python3 scale.py out
scale-in:
	python3 scale.py in
failover:
	python3 fault.py process
disk-failure:
	python3 fault.py disk
scale-up scale-down:
	python3 operations.py $@
rolling-restart:
	python3 operations.py rolling
chaos-node:
	python3 operations.py node
chaos-operator:
	python3 operations.py operator
chaos-wal-quorum:
	python3 operations.py wal-quorum
test-production:
	$(MAKE) test
	$(MAKE) scale-out
	$(MAKE) scale-in
	$(MAKE) scale-up
	$(MAKE) scale-down
	$(MAKE) rolling-restart
	$(MAKE) failover
	$(MAKE) disk-failure
	$(MAKE) chaos-node
	$(MAKE) chaos-operator
	$(MAKE) chaos-wal-quorum
	$(MAKE) test
status:
	$(KUBECTL) -n $(NAMESPACE) get pods,greptimedbclusters
cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it client -- /bin/sh
down:
	$(RUN) drop
	$(HELM) uninstall telemetry greptime-operator --namespace $(NAMESPACE)
	$(KUBECTL) delete namespace $(NAMESPACE) --wait=true
kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	$(SUPPORT) docker-down
	rm -f kind-config.json client.json results/kubeconfig
	rm -rf config/generated .build

# Local build context is generated from the tracked source link.
clean-build:
	rm -rf .build
