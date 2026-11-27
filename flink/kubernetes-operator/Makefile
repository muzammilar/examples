SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
STORAGE_PROFILE ?= base
CLUSTER_NAME := flink-kubernetes-operator
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
KUBECTL := "$(CURDIR)/results/tools/kubectl" --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
NODE_IMAGE := kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
CHART_URL := https://downloads.apache.org/flink/flink-kubernetes-operator-1.16.1/flink-kubernetes-operator-1.16.1-helm.tgz
CHART_SHA := c10d08e6246c987bc4513fb198112a52903eccec5616161e429c4f4a466c9181266c226509f591ec26e45a3ffb104febd5bb7c371c3c8132fe58cac40603d4fe

.PHONY: tools preflight kind-up build up test task-recovery storage-expand verify status cli report down kind-down
tools:
	python3 scripts/tools.py

preflight:
	python3 scripts/capacity.py

kind-up: tools
	mkdir -p results
	@if kind get clusters | grep -Fxq '$(CLUSTER_NAME)'; then test -s '$(KUBECONFIG)'; else python3 scripts/capacity.py; kind create cluster --name $(CLUSTER_NAME) --image $(NODE_IMAGE) --config deploy/kind.yaml --wait 180s; fi

build:
	docker build -f docker/Dockerfile.flink -t flink-operator-orders:lab .
	docker build -f docker/Dockerfile.verifier -t flink-operator-objects:lab .
	kind load docker-image --name $(CLUSTER_NAME) flink-operator-orders:lab flink-operator-objects:lab

results/operator.tgz:
	mkdir -p results
	curl --fail --location $(CHART_URL) --output $@

up: tools results/operator.tgz build
	echo '$(CHART_SHA)  results/operator.tgz' | shasum -a 512 --check
	$(KUBECTL) create namespace flink-lab --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(HELM) upgrade --install flink-operator results/operator.tgz --namespace flink-lab -f deploy/operator-values.yaml --wait --timeout 300s
	$(KUBECTL) -n flink-lab apply -k deploy/storage/$(STORAGE_PROFILE)
	$(KUBECTL) -n flink-lab apply -f deploy/verifier.yaml
	$(KUBECTL) -n flink-lab rollout status deployment/seaweed --timeout=180s
	$(KUBECTL) -n flink-lab wait --for=condition=Ready pod/object-verifier --timeout=180s
	$(KUBECTL) -n flink-lab exec object-verifier -- python /lab/objects.py setup
	$(KUBECTL) -n flink-lab apply -f deploy/orders.yaml

# Test only observes the submitted job; it never rewrites or reseeds its output.
test:
	python3 scripts/job.py test
	$(MAKE) verify

task-recovery:
	$(KUBECTL) -n flink-lab wait --for=create flinkdeployment/orders --timeout=180s
	python3 scripts/job.py task-recovery
	$(MAKE) verify

storage-expand:
	$(KUBECTL) -n flink-lab patch deployment seaweed --type=json --patch-file deploy/storage/expand.json
	$(KUBECTL) -n flink-lab rollout status deployment/seaweed --timeout=180s
	$(KUBECTL) -n flink-lab exec object-verifier -- python /lab/objects.py setup

verify:
	$(KUBECTL) -n flink-lab exec object-verifier -- python /lab/objects.py verify | tee results/verification.json

report:
	python3 scripts/report.py

status:
	$(KUBECTL) -n flink-lab get pods,flinkdeployments,pvc -o wide

cli:
	$(KUBECTL) -n flink-lab exec -it deployment/orders -- /opt/flink/bin/sql-client.sh -Drest.address=orders-rest

down:
	$(KUBECTL) -n flink-lab delete flinkdeployment orders --ignore-not-found --wait=true --timeout=180s
	$(HELM) uninstall flink-operator --namespace flink-lab --ignore-not-found --wait --timeout 180s
	$(KUBECTL) delete namespace flink-lab --ignore-not-found --wait=true --timeout=180s

kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	rm -f '$(KUBECONFIG)'
	@for image in flink-operator-orders:lab flink-operator-objects:lab; do if docker image inspect $$image >/dev/null 2>&1; then docker image rm $$image; fi; done
	@test -z "$$(docker ps -aq --filter label=io.x-k8s.kind.cluster=$(CLUSTER_NAME))"
