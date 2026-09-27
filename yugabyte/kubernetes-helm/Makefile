CLUSTER_NAME := yugabyte
NAMESPACE := yb-demo
RELEASE := yb-demo
CHART_VERSION := 2026.1.2
IMAGE := yugabytedb/yugabyte:2026.1.2.0-b137
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
TSERVER := $(KUBECTL) -n $(NAMESPACE) exec -i yb-tserver-0 -c yb-tserver --
HOST := yb-tserver-0.yb-tservers.$(NAMESPACE)
MASTERS := yb-master-0.yb-masters.$(NAMESPACE):7100,yb-master-1.yb-masters.$(NAMESPACE):7100,yb-master-2.yb-masters.$(NAMESPACE):7100

.PHONY: up test status cli cli-ycql ui down kind-cluster chart

up: kind-cluster chart

# Side-loads the image when it is already in the local Docker (saves a ~850 MB pull).
# `kind load docker-image` fails on multi-arch images, so export one platform only.
kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	if docker image inspect $(IMAGE) >/dev/null 2>&1; then \
		docker save --platform linux/$$(docker version --format '{{.Server.Arch}}') $(IMAGE) | \
		docker exec -i $(CLUSTER_NAME)-control-plane ctr -n k8s.io images import --digests -; fi

chart:
	helm repo add yugabytedb https://charts.yugabyte.com >/dev/null 2>&1 || true
	helm repo update yugabytedb
	$(HELM) upgrade --install $(RELEASE) yugabytedb/yugabyte --version $(CHART_VERSION) \
		--namespace $(NAMESPACE) --create-namespace -f values.yaml --wait --timeout 15m
	until $(TSERVER) ysqlsh -h $(HOST) -c 'SELECT 1' >/dev/null 2>&1; do sleep 5; done
	$(KUBECTL) -n $(NAMESPACE) get pods -o wide

# ysql/*.sql through ysqlsh, ycql/*.cql through ycqlsh
test:
	for f in ysql/*.sql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(TSERVER) ysqlsh -h $(HOST) -v ON_ERROR_STOP=1 -f - < $$f || exit 1; done
	for f in ycql/*.cql; do echo "-- $$f"; cat $$f; echo "-- output:"; $(TSERVER) ycqlsh $(HOST) < $$f || exit 1; done

status:
	$(TSERVER) yb-admin --master_addresses $(MASTERS) list_all_masters
	$(TSERVER) yb-admin --master_addresses $(MASTERS) list_all_tablet_servers
	$(KUBECTL) -n $(NAMESPACE) get pods,pvc

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it yb-tserver-0 -c yb-tserver -- ysqlsh -h $(HOST)

cli-ycql:
	$(KUBECTL) -n $(NAMESPACE) exec -it yb-tserver-0 -c yb-tserver -- ycqlsh $(HOST)

# master UI on http://localhost:7000, yugabyted UI on http://localhost:15433
ui:
	$(KUBECTL) -n $(NAMESPACE) port-forward svc/yb-masters 7000:7000 15433:15433

down:
	kind delete cluster --name $(CLUSTER_NAME)
