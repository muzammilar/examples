CLUSTER_NAME := manticore-helm
NAMESPACE := manticore
RELEASE := manticore
CHART_VERSION := 29.9.1
APP_VERSION := 29.9.0.1
LOAD_IMAGE := manticore-helm-load:local
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
WORKER := $(RELEASE)-manticoresearch-worker
SQL = $(KUBECTL) -n $(NAMESPACE) exec -i $(WORKER)-0 -c worker -- mysql -h127.0.0.1 -P9306 --table

.PHONY: up kind-cluster images install test failover scale-out scale-in status cli down

up: kind-cluster images install

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# the chart images (~340 MB compressed each) are pulled by the kind node itself (no copy on the
# host); the load client is built here and imported with `docker save --platform` + ctr import
# (`kind load docker-image` fails for multi-platform images on Docker Desktop)
ARCH := $(shell docker version -f '{{.Server.Arch}}')
images:
	docker build -q -t $(LOAD_IMAGE) client
	for i in $(LOAD_IMAGE); do echo "==> $$i"; \
		docker save --platform linux/$(ARCH) $$i | docker exec -i $(CLUSTER_NAME)-control-plane \
		ctr -n k8s.io images import --digests - >/dev/null || exit 1; done

install:
	helm repo add manticoresearch https://helm.manticoresearch.com >/dev/null 2>&1 || true
	helm repo update manticoresearch
	time $(HELM) upgrade --install $(RELEASE) manticoresearch/manticoresearch --version $(CHART_VERSION) \
		--namespace $(NAMESPACE) --create-namespace --values values.yaml --wait --timeout 10m
	$(KUBECTL) -n $(NAMESPACE) rollout status statefulset/$(WORKER) --timeout 5m
	$(KUBECTL) -n $(NAMESPACE) get pods -o wide

# table created on worker-0 (auto-added to the cluster), written through the worker Service,
# read on every worker and through the balancer
test:
	sh scripts/test.sh

# Go load (client/) as a pod writing to and reading from the 3 workers; KILL_AFTER s in,
# worker-1 is force-deleted; the StatefulSet recreates it and it rejoins
failover:
	sh scripts/failover.sh

# kubectl scale the worker StatefulSet 3 -> 5 and 5 -> 3 with the load running
scale-out:
	sh scripts/scale.sh 5

scale-in:
	sh scripts/scale.sh 3

status:
	$(KUBECTL) -n $(NAMESPACE) get pods,svc,pvc -o wide
	@for p in $$($(KUBECTL) -n $(NAMESPACE) get pods -l app.kubernetes.io/component=worker -o name); do \
		$(KUBECTL) -n $(NAMESPACE) exec $$p -c worker -- mysql -h127.0.0.1 -P9306 --skip-table -N -e \
		"SHOW STATUS LIKE 'cluster_manticore_cluster_%'" | awk -v p=$${p#pod/} -F'\t' '$$1 ~ /_(status|size|node_state|indexes)$$/ { printf "%s %s=%s  ", p, $$1, $$2 } END { print "" }'; done

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it $(WORKER)-0 -c worker -- mysql -h127.0.0.1 -P9306

down:
	kind delete cluster --name $(CLUSTER_NAME)
	-docker image rm $(LOAD_IMAGE)
