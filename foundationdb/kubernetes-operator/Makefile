CLUSTER_NAME := fdb-operator
NAMESPACE := fdb
OPERATOR_VERSION := v2.37.0
OPERATOR_URL := https://raw.githubusercontent.com/FoundationDB/fdb-kubernetes-operator/$(OPERATOR_VERSION)/config
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
FDB_POD = $$($(KUBECTL) get pod -l foundationdb.org/fdb-process-class=log -o name | head -1)

.PHONY: up test status cli metrics down kind-cluster operator cluster exporter

up: kind-cluster operator cluster exporter

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

# CRDs + the namespace-scoped operator from the operator repo. The sample
# ClusterRoleBinding carries a placeholder namespace; point it at ours.
operator:
	kubectl --context kind-$(CLUSTER_NAME) apply --server-side \
		-f $(OPERATOR_URL)/crd/bases/apps.foundationdb.org_foundationdbclusters.yaml \
		-f $(OPERATOR_URL)/crd/bases/apps.foundationdb.org_foundationdbbackups.yaml \
		-f $(OPERATOR_URL)/crd/bases/apps.foundationdb.org_foundationdbrestores.yaml
	curl -fsSL $(OPERATOR_URL)/samples/deployment.yaml \
		| sed 's/namespace: metadata.namespace/namespace: $(NAMESPACE)/' \
		| $(KUBECTL) apply -f -
	$(KUBECTL) rollout status deployment/fdb-kubernetes-operator-controller-manager --timeout=15m

cluster:
	$(KUBECTL) apply -f cluster.yaml
	$(KUBECTL) wait foundationdbcluster/test-cluster --for=jsonpath='{.status.health.available}'=true --timeout=30m
	$(KUBECTL) get foundationdbcluster,pods

exporter:
	$(KUBECTL) apply -f exporter.yaml
	$(KUBECTL) rollout status deployment/fdb-exporter --timeout=10m

# every line of queries/test.fdbcli is run as one fdbcli command
test:
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec "$$(paste -sd ';' queries/test.fdbcli)"

status:
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec status

cli:
	$(KUBECTL) exec -it $(FDB_POD) -c foundationdb -- fdbcli

# curl localhost:9444/metrics while this runs
metrics:
	$(KUBECTL) port-forward svc/fdb-exporter 9444:9444

down:
	kind delete cluster --name $(CLUSTER_NAME)
