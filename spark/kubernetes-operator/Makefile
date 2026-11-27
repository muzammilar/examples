SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.NOTPARALLEL:
CLUSTER_NAME := spark-kubernetes-operator
KUBECONFIG := $(CURDIR)/results/kubeconfig
TMPDIR := $(CURDIR)/results/tmp
export KUBECONFIG TMPDIR
KUBECTL := "$(CURDIR)/results/tools/kubectl" --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
NODES := $(CLUSTER_NAME)-worker,$(CLUSTER_NAME)-worker2,$(CLUSTER_NAME)-worker3
NODE_IMAGE := kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
CHART_SHA256 := a8bc69c6f69a794cdf6a00b43907501a88682ca977719a51d33be74def7a75a8

.PHONY: tools prepare kind-up build up test verify verify-data executor-recovery datanode-replace test-production status cli down kind-down
tools:
	mkdir -p results/tmp
	python3 scripts/tools.py
prepare:
	mkdir -p results/tmp
	python3 scripts/prepare.py
	git check-ignore .build/orders.py .build/verify.py .build/source-inventory.json
kind-up: tools
	@if kind get clusters | grep -Fxq '$(CLUSTER_NAME)'; then test -s '$(KUBECONFIG)'; else python3 scripts/capacity.py create; kind create cluster --name $(CLUSTER_NAME) --image $(NODE_IMAGE) --config deploy/kind.yaml --wait 180s; fi
build: prepare
	docker build -f orders-workload/Dockerfile -t examples/spark-hdfs-orders:4.3.0 .build
	docker build --target hadoop -f docker/Dockerfile.hadoop -t examples/spark-hdfs-storage:3.5.0 .build
	docker build --target parquet-verifier -f docker/Dockerfile.hadoop -t examples/spark-hdfs-verifier:26.0.1 .build
results/spark-kubernetes-operator-1.9.0.tgz:
	mkdir -p results/tmp
	$(HELM) pull spark-kubernetes-operator --repo https://apache.github.io/spark-kubernetes-operator --version 1.9.0 --destination results
up: tools build results/spark-kubernetes-operator-1.9.0.tgz
	python3 scripts/capacity.py load
	kind load docker-image --name $(CLUSTER_NAME) --nodes $(NODES) examples/spark-hdfs-orders:4.3.0 examples/spark-hdfs-storage:3.5.0 examples/spark-hdfs-verifier:26.0.1
	echo '$(CHART_SHA256)  results/spark-kubernetes-operator-1.9.0.tgz' | shasum -a 256 --check
	$(KUBECTL) create namespace spark-lab --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(HELM) upgrade --install spark-operator results/spark-kubernetes-operator-1.9.0.tgz --namespace spark-lab -f deploy/operator-values.yaml --wait --timeout 300s
	$(KUBECTL) -n spark-lab apply -f deploy/hdfs.yaml -f deploy/storage-tools.yaml
	$(KUBECTL) -n spark-lab rollout status statefulset/namenode --timeout=180s
	$(KUBECTL) -n spark-lab rollout status statefulset/datanode --timeout=180s
	$(KUBECTL) -n spark-lab wait --for=condition=Ready pod/hdfs-tools --timeout=180s
	python3 scripts/lifecycle.py setup
test:
	python3 scripts/lifecycle.py test
	python3 scripts/storage.py audit
executor-recovery:
	python3 scripts/lifecycle.py executor-recovery
	python3 scripts/storage.py audit
verify-data:
	python3 scripts/lifecycle.py verify
verify: verify-data
	python3 scripts/storage.py audit
datanode-replace:
	python3 scripts/storage.py replace
test-production:
	$(MAKE) executor-recovery
	$(MAKE) datanode-replace
status:
	$(KUBECTL) -n spark-lab get pods,sparkapplications,pvc -o wide
cli:
	$(KUBECTL) -n spark-lab exec -it hdfs-tools -- bash
down:
	$(KUBECTL) -n spark-lab delete sparkapplications --all --ignore-not-found --wait=true --timeout=180s
	$(HELM) uninstall spark-operator --namespace spark-lab --ignore-not-found --wait --timeout 180s
	$(KUBECTL) delete namespace spark-lab --ignore-not-found --wait=true --timeout=180s
kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	rm -f '$(KUBECONFIG)'
	@for image in examples/spark-hdfs-orders:4.3.0 examples/spark-hdfs-storage:3.5.0 examples/spark-hdfs-verifier:26.0.1; do if docker image inspect $$image >/dev/null 2>&1; then docker image rm $$image; fi; done
	@test -z "$$(docker ps -aq --filter label=io.x-k8s.kind.cluster=$(CLUSTER_NAME))"
