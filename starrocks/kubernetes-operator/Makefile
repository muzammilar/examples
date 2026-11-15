SHELL := /bin/sh
CLUSTER_NAME := starrocks-operator
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n starrocks-lab
HELM := helm --kube-context kind-$(CLUSTER_NAME)
COUNT ?= 1000000
FAULT_BATCH_ROWS ?= 1000
FAULT_PAUSE_MS ?= 1000
export COUNT FAULT_BATCH_ROWS FAULT_PAUSE_MS
RUN := $(KUBECTL) exec orders-workload -- client
.NOTPARALLEL:
.PHONY: kind-up up test benchmark status down kind-down test-production
kind-up:
	python3 lifecycle.py starrocks-operator-support starrocks-operator
	python3 prepare.py mutable-orders
	docker build -t starrocks-operator-workload:dev .build/mutable-orders
	docker pull starrocks/fe-ubuntu:4.1.7
	docker pull starrocks/be-ubuntu:4.1.7
	docker pull starrocks/operator:v1.11.8
	python3 config.py
	@kind get clusters | rg -qx '$(CLUSTER_NAME)' || kind create cluster --name $(CLUSTER_NAME) --image kindest/node:v1.34.0 --config kind-config.json
	python3 load_images.py
up:
	$(HELM) upgrade --install orders-operator https://github.com/StarRocks/starrocks-kubernetes-operator/releases/download/v1.11.8/operator-1.11.8.tgz --namespace starrocks-lab --create-namespace --values operator-values.yaml --wait --timeout 5m
	$(KUBECTL) apply -f cluster.json
	$(KUBECTL) rollout status statefulset/orders-fe --timeout=600s
	$(KUBECTL) rollout status statefulset/orders-be --timeout=600s
	$(KUBECTL) apply -f workload.json
	$(KUBECTL) wait --for=condition=Ready pod/orders-workload --timeout=120s
	python3 operations.py ready
	$(RUN) setup
	@if test -f results/seed.json; then $(RUN) verify; else $(RUN) seed; fi
test:
	python3 -c 'from pathlib import Path; import time; r=Path("results"); [(p.rename(p.with_name(p.stem+"-before-"+str(time.time_ns())+".json"))) for n in ("correctness.json", "recovery-history.json") if (p:=r/n).exists()]'
	$(RUN) verify
	python3 -c 'import json; from pathlib import Path; r=Path("results"); assert json.load(open(r/"correctness.json")); assert not list(r.glob("fault-*.jsonl")) or json.load(open(r/"recovery-history.json")), "missing fresh recovery reconciliation"'
benchmark:
	python3 -c 'from pathlib import Path; import time; p=Path("results/query-benchmark.json"); p.exists() and p.rename(p.with_name("query-benchmark-before-"+str(time.time_ns())+".json"))'
	$(RUN) benchmark
	python3 -c 'import json; d=json.load(open("results/query-benchmark.json")); assert len(d)==3 and all(v["samples"]==20 and v["errors"]==0 for v in d.values()), "incomplete benchmark results"'
	test -s results/query-benchmark.json
scale-out scale-in scale-up scale-down rolling-restart failover chaos-fe-leader chaos-node chaos-operator disk-failure chaos-network chaos-write-quorum drain-test:
	python3 operations.py $@
test-production:
	$(MAKE) test
	$(MAKE) scale-out
	$(MAKE) scale-in
	$(MAKE) scale-up
	$(MAKE) scale-down
	$(MAKE) rolling-restart
	$(MAKE) failover
	$(MAKE) chaos-fe-leader
	$(MAKE) chaos-node
	$(MAKE) chaos-operator
	$(MAKE) chaos-network
	$(MAKE) chaos-write-quorum
	$(MAKE) drain-test
	$(MAKE) disk-failure
	$(MAKE) test
status:
	$(KUBECTL) get pods,pvc,starrockscluster,pdb
down:
	$(RUN) drop
	$(KUBECTL) delete starrockscluster orders --wait=true --timeout=180s
	$(HELM) uninstall orders-operator --namespace starrocks-lab
	$(KUBECTL) delete namespace starrocks-lab --wait=true
kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	docker image rm starrocks-operator-workload:dev
	rm -rf .build kind-config.json cluster.json workload.json results/kubeconfig
