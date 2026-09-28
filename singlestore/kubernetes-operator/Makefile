CLUSTER_NAME := singlestore
NAMESPACE := singlestore
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
KUBECTL_NS := $(KUBECTL) -n $(NAMESPACE)
MA_POD := node-sdb-cluster-master-0
# demo password; its hash is adminHashedPassword in sdb-cluster.yaml
ADMIN_PASSWORD := SingleStore-Demo-1
# node counts patched into sdb-cluster.yaml; a Dev Image license only allows 0 child aggregators and 1 leaf
SINGLESTORE_CHILD_AGGREGATORS ?= 1
SINGLESTORE_LEAVES ?= 2
SQL := $(KUBECTL_NS) exec -i $(MA_POD) -c node -- env MYSQL_PWD=$(ADMIN_PASSWORD) \
	/usr/lib/singlestore-client/singlestore-client --no-defaults --protocol=tcp -h 127.0.0.1 -P 3306 -u admin

.PHONY: up test status cli down kind-cluster operator cluster

up: kind-cluster operator cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# CRD, RBAC and the operator Deployment, as in the SingleStore Kubernetes deployment guide
operator:
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) apply -f sdb-cluster-crd.yaml
	$(KUBECTL_NS) apply -f sdb-rbac.yaml -f sdb-operator.yaml
	$(KUBECTL_NS) rollout status deployment/sdb-operator --timeout=15m

# the operator refuses a MemsqlCluster without a license (and the engine cannot bootstrap without one):
# SINGLESTORE_LICENSE goes into a Secret
cluster:
	@test -n "$$SINGLESTORE_LICENSE" || { echo "SINGLESTORE_LICENSE is not set (get a key at https://portal.singlestore.com)"; exit 1; }
	@$(KUBECTL_NS) create secret generic singlestore-license --from-literal=license="$$SINGLESTORE_LICENSE" \
		--dry-run=client -o yaml | $(KUBECTL_NS) apply -f -
	sed -e '/childAggregatorSpec:/,/count:/ s/count: .*/count: $(SINGLESTORE_CHILD_AGGREGATORS)/' \
		-e '/leafSpec:/,/count:/ s/count: .*/count: $(SINGLESTORE_LEAVES)/' sdb-cluster.yaml | $(KUBECTL_NS) apply -f -
	@echo "waiting for MemsqlCluster phase Running (first run pulls the ~1.4 GB singlestore/node image)"
	for i in $$(seq 720); do [ "$$($(KUBECTL_NS) get memsqlcluster sdb-cluster -o jsonpath='{.status.phase}')" = Running ] && break; sleep 5; done
	[ "$$($(KUBECTL_NS) get memsqlcluster sdb-cluster -o jsonpath='{.status.phase}')" = Running ]
	$(KUBECTL_NS) get memsqlcluster,pods,svc

# the client does not echo statements, so each sql/*.sql file is printed before its output
test:
	for f in sql/*.sql; do echo; echo "==== $$f"; cat $$f; echo "---- output"; \
		$(SQL) --table < $$f || exit 1; done

status:
	$(KUBECTL_NS) get memsqlcluster,pods
	$(SQL) --table -e "SHOW AGGREGATORS; SHOW LEAVES; \
		SELECT DATABASE_NAME, HOST, ROLE, COUNT(*) AS partitions FROM information_schema.DISTRIBUTED_PARTITIONS GROUP BY 1, 2, 3 ORDER BY 1, 2, 3"

cli:
	$(KUBECTL_NS) exec -it $(MA_POD) -c node -- env MYSQL_PWD=$(ADMIN_PASSWORD) \
		/usr/lib/singlestore-client/singlestore-client --no-defaults --protocol=tcp -h 127.0.0.1 -P 3306 -u admin

down:
	kind delete cluster --name $(CLUSTER_NAME)
