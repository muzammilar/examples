CLUSTER_NAME := scylla-operator
NAMESPACE := scylla
OPERATOR_VERSION := v1.22.0
EXAMPLES_URL := https://raw.githubusercontent.com/scylladb/scylla-operator/$(OPERATOR_VERSION)/examples
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
SCYLLA_POD := scylla-dc1-rack1-0
FAILOVER_POD := scylla-dc1-rack1-2
NODETOOL := $(KUBECTL) -n $(NAMESPACE) exec $(SCYLLA_POD) -c scylla -- nodetool
CQLSH := $(KUBECTL) -n $(NAMESPACE) exec -i $(SCYLLA_POD) -c scylla -- cqlsh

.PHONY: up test failover status cli grafana down kind-cluster operator cluster monitoring

up: kind-cluster operator cluster monitoring

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	$(KUBECTL) apply -f sysctl-daemonset.yaml

# cert-manager (operator webhook certs) and prometheus-operator (must exist
# before scylla-operator starts, or ScyllaDBMonitoring stays disabled).
operator:
	$(KUBECTL) apply --server-side -f $(EXAMPLES_URL)/third-party/cert-manager.yaml
	$(KUBECTL) -n cert-manager wait --for=condition=available deployment --all --timeout=15m
	$(KUBECTL) apply --server-side -f $(EXAMPLES_URL)/third-party/prometheus-operator.yaml
	$(KUBECTL) -n prometheus-operator rollout status deployment/prometheus-operator --timeout=15m
	helm repo add scylla https://scylla-operator-charts.storage.googleapis.com/stable >/dev/null 2>&1 || true
	helm repo update scylla
	$(HELM) upgrade --install scylla-operator scylla/scylla-operator \
		--version $(OPERATOR_VERSION) --namespace scylla-operator --create-namespace \
		--set replicas=1 --set webhookServerReplicas=1 --wait --timeout 15m

cluster:
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f scyllacluster.yaml
	$(KUBECTL) -n $(NAMESPACE) wait scyllacluster/scylla --for=condition=Available --timeout=15m
	$(KUBECTL) -n $(NAMESPACE) get scyllacluster,pods

monitoring:
	$(KUBECTL) -n $(NAMESPACE) create serviceaccount prometheus --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -n $(NAMESPACE) --server-side \
		-f $(EXAMPLES_URL)/monitoring/v1alpha1/prometheus.clusterrole.yaml \
		-f $(EXAMPLES_URL)/monitoring/v1alpha1/prometheus.clusterrolebinding.yaml \
		-f $(EXAMPLES_URL)/monitoring/v1alpha1/prometheus.service.yaml
	$(KUBECTL) apply -f prometheus.yaml -f scylladbmonitoring.yaml
	$(KUBECTL) -n $(NAMESPACE) wait scylladbmonitoring/scylla --for=condition=Available --timeout=15m

# RF=3 keyspace, a few writes/reads at QUORUM
test:
	$(KUBECTL) -n $(NAMESPACE) exec -i $(SCYLLA_POD) -c scylla -- cqlsh < cql/test.cql

# delete one member pod: QUORUM reads/writes and LWT keep working with 2 of 3 replicas,
# ALL fails; the StatefulSet recreates the pod (same PVC) and it rejoins as UN
failover:
	@echo "==> deleting pod $(FAILOVER_POD)"
	$(KUBECTL) -n $(NAMESPACE) delete pod $(FAILOVER_POD) --wait=false
	@echo "==> waiting until $(SCYLLA_POD) sees it down (DN)"
	@for i in $$(seq 60); do $(NODETOOL) status 2>/dev/null | grep -q '^DN' && break; sleep 2; done
	$(NODETOOL) status | grep -E '^(UN|DN)'
	@$(NODETOOL) status | grep -q '^DN'
	@echo "==> QUORUM write + read (2 of 3 replicas)"
	$(CQLSH) < failover/quorum.cql
	@echo "==> CONSISTENCY ALL (3 of 3 replicas)"
	@if $(CQLSH) < failover/all.cql; then \
		if $(NODETOOL) status | grep -q '^DN'; then echo "unexpected: ALL succeeded with a node down"; exit 1; fi; \
		echo "note: $(FAILOVER_POD) was already back up"; \
	else \
		echo "expected: Unavailable, only 2 of 3 replicas alive"; \
	fi
	@echo "==> lightweight transactions (Paxos quorum)"
	$(CQLSH) < failover/lwt.cql
	@echo "==> waiting for $(FAILOVER_POD) to be Ready and all three nodes UN (up to 180 x 5 s)"
	@for i in $$(seq 180); do \
		[ "$$($(NODETOOL) status 2>/dev/null | grep -c '^UN')" = 3 ] && \
		[ "$$($(KUBECTL) -n $(NAMESPACE) get pod $(FAILOVER_POD) -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = True ] && break; \
		[ $$i -lt 180 ] || { echo 'not back after 900 s'; exit 1; }; sleep 5; done
	$(NODETOOL) status | grep -E '^(UN|DN)'
	@echo "==> CONSISTENCY ALL works again (all 3 replicas answer)"
	$(CQLSH) < failover/all.cql
	$(KUBECTL) -n $(NAMESPACE) wait scyllacluster/scylla --for=condition=Available --timeout=15m

status:
	$(KUBECTL) -n $(NAMESPACE) exec $(SCYLLA_POD) -c scylla -- nodetool status

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it $(SCYLLA_POD) -c scylla -- cqlsh

# https://localhost:3000 (self-signed certificate); credentials printed first
grafana:
	@echo "user: $$($(KUBECTL) -n $(NAMESPACE) get secret scylla-grafana-admin-credentials -o jsonpath='{.data.username}' | base64 -d)"
	@echo "pass: $$($(KUBECTL) -n $(NAMESPACE) get secret scylla-grafana-admin-credentials -o jsonpath='{.data.password}' | base64 -d)"
	$(KUBECTL) -n $(NAMESPACE) port-forward svc/scylla-grafana 3000:3000

down:
	kind delete cluster --name $(CLUSTER_NAME)
