CLUSTER_NAME := ydb-operator
NAMESPACE := ydb
OPERATOR_VERSION := 0.8.0
PROM_STACK_VERSION := 91.8.0
YDB_IMAGE := cr.yandex/crptqonuodf51kdj7a7d/ydb:26.2.1.14
YDB_IMAGE_TAR ?= ydb.tar
# the operator image is on cr.yandex too (37 MB, but its pull alone took ~10 min in a test)
OPERATOR_IMAGE := cr.yandex/yc/ydb-kubernetes-operator:$(OPERATOR_VERSION)
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
YDB_EXEC = $(KUBECTL) -n $(NAMESPACE) exec -i database-0 -c ydb-dynamic -- /opt/ydb/bin/ydb
YDB = $(YDB_EXEC) -e grpc://localhost:2135 -d /Root/database
# the CLI checks for a newer release on every call (and warns: the image has no curl);
# `version --disable-checks` turns that off for the pod's ydb user
NO_VERSION_CHECK = $(YDB_EXEC) version --disable-checks >/dev/null

.PHONY: up test status cli grafana down kind-cluster prometheus operator cluster monitoring

up: kind-cluster prometheus operator cluster monitoring

# cr.yandex can be slow from outside Russia; if the image is already in local
# Docker (`docker pull $(YDB_IMAGE)`), or saved to $(YDB_IMAGE_TAR)
# (`docker save --platform linux/amd64 -o ydb.tar $(YDB_IMAGE)`), copy it onto
# every kind worker instead (--digests also registers it under its digest). The same
# for $(OPERATOR_IMAGE) if it is in local Docker (`docker pull --platform linux/amd64`).
kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	for n in $$(kind get nodes --name $(CLUSTER_NAME) | grep worker); do \
		if [ -f "$(YDB_IMAGE_TAR)" ]; then docker exec -i $$n ctr -n k8s.io images import --digests - < $(YDB_IMAGE_TAR); \
		elif docker image inspect $(YDB_IMAGE) >/dev/null 2>&1; then \
			docker save --platform linux/amd64 $(YDB_IMAGE) | docker exec -i $$n ctr -n k8s.io images import --digests -; fi; \
		if docker image inspect $(OPERATOR_IMAGE) >/dev/null 2>&1; then \
			docker save --platform linux/amd64 $(OPERATOR_IMAGE) | docker exec -i $$n ctr -n k8s.io images import --digests -; fi; done
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -

# Prometheus, Grafana and the ServiceMonitor CRD the YDB monitoring objects need.
# No --wait: only the CRDs are needed before continuing; pods come up alongside.
prometheus:
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
	helm repo update prometheus-community
	$(HELM) upgrade --install prom prometheus-community/kube-prometheus-stack --version $(PROM_STACK_VERSION) \
		--namespace monitoring --create-namespace \
		--set alertmanager.enabled=false \
		--set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false

# the chart still points its webhook cert job at the frozen k8s.gcr.io registry;
# metrics.enabled turns on --with-service-monitors (needed by monitoring.yaml)
operator:
	helm repo add ydb https://charts.ydb.tech/ >/dev/null 2>&1 || true
	helm repo update ydb
	$(HELM) upgrade --install ydb-operator ydb/ydb-operator --version $(OPERATOR_VERSION) \
		--namespace ydb-operator --create-namespace --wait --timeout 20m \
		--set webhook.patch.image.repository=registry.k8s.io/ingress-nginx/kube-webhook-certgen \
		--set metrics.enabled=true

cluster:
	$(KUBECTL) -n $(NAMESPACE) apply -f storage.yaml
	$(KUBECTL) -n $(NAMESPACE) wait storage/storage --for=jsonpath='{.status.state}'=Ready --timeout=15m
	$(KUBECTL) -n $(NAMESPACE) apply -f database.yaml
	$(KUBECTL) -n $(NAMESPACE) wait database/database --for=jsonpath='{.status.state}'=Ready --timeout=15m
	$(KUBECTL) -n $(NAMESPACE) get storage,database,pods -o wide

monitoring:
	$(KUBECTL) -n $(NAMESPACE) apply -f monitoring.yaml

# YDB runs DDL and DML as separate queries (mixing them in one fails with "Queries
# with mixed data and scheme operations are not supported"), hence one file each
test:
	@$(NO_VERSION_CHECK)
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; $(YDB) sql -f - < $$f || exit 1; done

status:
	@$(NO_VERSION_CHECK)
	$(YDB) discovery list
	$(KUBECTL) -n $(NAMESPACE) get storage,database

# no subcommand = interactive mode
cli:
	@$(NO_VERSION_CHECK)
	$(KUBECTL) -n $(NAMESPACE) exec -it database-0 -c ydb-dynamic -- /opt/ydb/bin/ydb -e grpc://localhost:2135 -d /Root/database

# http://localhost:3000; the chart generates the admin password. Grafana is
# installed without --wait, so wait for it here before port-forwarding.
grafana:
	$(KUBECTL) -n monitoring rollout status deploy/prom-grafana --timeout=15m
	@echo "user: admin  password: $$($(KUBECTL) -n monitoring get secret prom-grafana -o jsonpath='{.data.admin-password}' | base64 -d)"
	$(KUBECTL) -n monitoring port-forward svc/prom-grafana 3000:80

down:
	kind delete cluster --name $(CLUSTER_NAME)
