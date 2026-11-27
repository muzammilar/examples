SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.NOTPARALLEL:
CLUSTER := doris-kubernetes-operator
KUBECONFIG := $(CURDIR)/results/kubeconfig
TMPDIR := $(CURDIR)/results/tmp
export KUBECONFIG TMPDIR
KUBECTL := "$(CURDIR)/results/tools/kubectl" --context kind-$(CLUSTER)
REVISION ?= 1
NODES := $(CLUSTER)-worker $(CLUSTER)-worker2 $(CLUSTER)-worker3
FE := apache/doris:fe-4.0.8@sha256:390413325a93d9121768bda153ccaa3c7a8da4aa0fd2a466406cddc14a6399b2
BE := apache/doris:be-4.0.8@sha256:d911ae3826491598be4e383cba83011a266091b8fdc6c34c036280220cc4fea9
NODE_IMAGE := kindest/node:v1.37.0@sha256:a1ed56cfb0e7b93589bdf97c8cd566405a265939e3620fc4f5de89adff580ae5
.PHONY: tools prepare kind-up build up test verify mutate status cli down kind-down
tools:
	mkdir -p results/tmp
	python3 scripts/tools.py
prepare: tools
	python3 scripts/prepare.py
	git check-ignore .build/orders-workload/main.go .build/operator/operator.yaml
kind-up: tools
	@if kind get clusters | grep -Fxq '$(CLUSTER)'; then test -s '$(KUBECONFIG)'; else python3 scripts/capacity.py create; kind create cluster --name $(CLUSTER) --image $(NODE_IMAGE) --config deploy/kind.yaml --wait 180s; fi
build: prepare
	docker build -f orders-workload/Dockerfile -t examples/doris-operator-orders:4.0.8 .build/orders-workload
up: build
	python3 scripts/capacity.py load
	docker image inspect '$(FE)' >/dev/null 2>&1 || docker pull '$(FE)'
	docker image inspect '$(BE)' >/dev/null 2>&1 || docker pull '$(BE)'
	docker tag '$(FE)' apache/doris:fe-4.0.8
	docker tag '$(BE)' apache/doris:be-4.0.8
	@for node in $(NODES); do python3 scripts/capacity.py worker $$node; python3 scripts/load_images.py $$node; done
	python3 scripts/capacity.py run
	$(KUBECTL) wait --for=condition=Ready nodes --all --timeout=60s
	$(KUBECTL) wait --for=condition=DiskPressure=false nodes --all --timeout=60s
	$(KUBECTL) apply --server-side -f results/operator-rendered.yaml
	$(KUBECTL) -n doris-lab rollout status deployment/doris-operator --timeout=180s
	$(KUBECTL) -n doris-lab create configmap orders-fe-config --from-file=config/fe.conf --dry-run=client -o yaml | $(KUBECTL) -n doris-lab apply -f -
	$(KUBECTL) -n doris-lab create configmap orders-be-config --from-file=config/be.conf --dry-run=client -o yaml | $(KUBECTL) -n doris-lab apply -f -
	$(KUBECTL) -n doris-lab apply -f deploy/cluster.yaml
	$(KUBECTL) -n doris-lab wait --for=create statefulset/orders-fe --timeout=120s
	$(KUBECTL) -n doris-lab rollout status statefulset/orders-fe --timeout=300s
	$(KUBECTL) -n doris-lab wait --for=create statefulset/orders-be --timeout=180s
	$(KUBECTL) -n doris-lab rollout status statefulset/orders-be --timeout=300s
	python3 scripts/readiness.py
	python3 scripts/workload.py setup
test:
	python3 scripts/workload.py seed
	$(MAKE) verify
verify:
	python3 scripts/workload.py verify $(REVISION) 0
	python3 scripts/workload.py verify $(REVISION) 1
	python3 scripts/workload.py verify $(REVISION) 2
	python3 scripts/topology.py
mutate:
	python3 scripts/workload.py mutate
	python3 scripts/workload.py verify 2 0
	python3 scripts/workload.py verify 2 1
	python3 scripts/workload.py verify 2 2
	python3 scripts/topology.py
status:
	$(KUBECTL) -n doris-lab get pods,dorisclusters,pvc -o wide
cli:
	$(KUBECTL) -n doris-lab exec -it orders-fe-0 -- mysql -h127.0.0.1 -P9030 -uroot
down:
	$(KUBECTL) -n doris-lab delete doriscluster orders --ignore-not-found --wait=true --timeout=180s
	$(KUBECTL) delete namespace doris-lab --ignore-not-found --wait=true --timeout=180s
kind-down:
	kind delete cluster --name $(CLUSTER)
	rm -f '$(KUBECONFIG)'
	@if docker image inspect examples/doris-operator-orders:4.0.8 >/dev/null 2>&1; then docker image rm examples/doris-operator-orders:4.0.8; fi
	@test -z "$$(docker ps -aq --filter label=io.x-k8s.kind.cluster=$(CLUSTER))"

.PHONY: backend-restart operator-restart test-production
backend-restart:
	python3 scripts/recovery.py backend $(REVISION)
operator-restart:
	python3 scripts/recovery.py operator $(REVISION)
test-production:
	$(MAKE) test
	$(MAKE) backend-restart
	$(MAKE) operator-restart
