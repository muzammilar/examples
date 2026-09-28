CLUSTER_NAME := fdb-operator
NAMESPACE := fdb
OPERATOR_VERSION := v2.37.0
OPERATOR_URL := https://raw.githubusercontent.com/FoundationDB/fdb-kubernetes-operator/$(OPERATOR_VERSION)/config
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
FDB_POD = $$($(KUBECTL) get pod -l foundationdb.org/fdb-process-class=log -o name | head -1)

# images the operator sample and cluster.yaml pull (the 7.3/7.4 init images and the
# per-pod 7.3.79 image are the slow part inside kind)
PRELOAD_IMAGES := foundationdb/fdb-kubernetes-operator:$(OPERATOR_VERSION) \
	foundationdb/fdb-kubernetes-monitor:7.3.69 foundationdb/fdb-kubernetes-monitor:7.4.5 \
	foundationdb/fdb-kubernetes-monitor:7.3.79 aikoven/foundationdb-exporter:3.1.0

.PHONY: up test roles failover status cli metrics down kind-cluster preload operator cluster exporter

up: kind-cluster preload operator cluster exporter

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

# copies PRELOAD_IMAGES that already exist in the host's Docker into the kind node, so
# kind does not pull them again; images not present locally are skipped (pulled in-cluster)
preload:
	@for img in $(PRELOAD_IMAGES); do \
		plat=$$(docker image inspect --format '{{.Os}}/{{.Architecture}}' $$img 2>/dev/null) \
			|| { echo "preload: $$img not in local Docker, kind will pull it"; continue; }; \
		echo "preload: $$img ($$plat)"; \
		docker save --platform $$plat $$img | docker exec -i $(CLUSTER_NAME)-control-plane \
			ctr -n k8s.io images import --digests - >/dev/null || exit 1; \
	done

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
	$(KUBECTL) wait deployment/fdb-kubernetes-operator-controller-manager --for=condition=Available --timeout=15m

cluster:
	$(KUBECTL) apply -f cluster.yaml
	$(KUBECTL) wait foundationdbcluster/test-cluster --for=jsonpath='{.status.health.available}'=true --timeout=30m
	@# the operator has finished reconciling the current spec
	$(KUBECTL) wait foundationdbcluster/test-cluster --timeout=30m --for=jsonpath='{.status.generations.reconciled}'=$$($(KUBECTL) get foundationdbcluster/test-cluster -o jsonpath='{.metadata.generation}')
	$(KUBECTL) get foundationdbcluster,pods

exporter:
	$(KUBECTL) apply -f exporter.yaml
	$(KUBECTL) wait deployment/fdb-exporter --for=condition=Available --timeout=10m

# every line of queries/test.fdbcli is run as one fdbcli command, then the
# role view: which FDB roles each process (pod) currently holds
test:
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec "$$(paste -sd ';' queries/test.fdbcli)"
	@$(MAKE) --no-print-directory roles

roles:
	$(KUBECTL) get pods -L foundationdb.org/fdb-process-class
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec 'status json' \
		| jq -r '["ADDRESS","CLASS","ROLES"], ([.cluster.processes[]] | sort_by(.class_type, .address)[] | [.address, .class_type, ([.roles[].role] | join(","))]) | @tsv' \
		| column -t

# freeze (SIGSTOP) the fdbserver of one storage pod that holds data. The pod stays Running
# and its liveness probe (sidecar) passes, so Kubernetes and the operator replace nothing:
# FDB alone has to notice the process is gone, mark the storage server failed and
# re-replicate its data onto the remaining servers. Once FDB is healthy again (fault
# tolerance back to 1 without the frozen server) the process is resumed (SIGCONT) and
# rejoins. `status json` is polled every 2 s and every change of data state / healthy /
# fault tolerance / storage servers / unreachable processes is printed. Bounded: resumes
# after FREEZE_MAX_POLLS polls even without healing, gives up after 300 polls in total.
# The victim is the storage server with the most applied mutations (a fresh or emptied
# server has none, and freezing a server without data would not degrade anything).
FREEZE_MAX_POLLS ?= 150
FAILOVER_JQ := [.cluster.data.state.name, .cluster.data.state.healthy, \
	.cluster.fault_tolerance.max_zone_failures_without_losing_data, \
	([.cluster.processes[] | select(any(.roles[]?; .role == "storage"))] | length), \
	([.cluster.messages[]?.name] | any(. == "unreachable_processes"))] | @tsv
FAILOVER_VICTIM_JQ := [.cluster.processes[] | select(any(.roles[]?; .role == "storage")) \
	| {group: .locality.instance_id, mutations: ([.roles[] | select(.role == "storage") | .mutations.counter] | add)}] \
	| max_by(.mutations) | .group

failover:
	@lp=$(FDB_POD); \
	st() { $(KUBECTL) exec $$lp -c foundationdb -- fdbcli --exec 'status json' --timeout 5 2>/dev/null; }; \
	want=$$($(KUBECTL) get pod -l foundationdb.org/fdb-process-class=storage -o name | wc -l | tr -d ' '); \
	group=$$(st | jq -r '$(FAILOVER_VICTIM_JQ)'); \
	pod=$$($(KUBECTL) get pod -l foundationdb.org/fdb-process-group-id=$$group -o name); \
	[ -n "$$pod" ] || { echo "no storage pod found for process group '$$group'"; exit 1; }; \
	resume() { $(KUBECTL) exec $$pod -c foundationdb -- pkill -CONT -x fdbserver; }; \
	trap resume EXIT; trap 'exit 130' INT TERM; \
	$(KUBECTL) exec $$pod -c foundationdb -- pkill -STOP -x fdbserver || { trap - EXIT; exit 1; }; \
	echo "-- froze fdbserver in $$pod (SIGSTOP; $$want storage pods)"; \
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' SECONDS PHASE DATA_STATE HEALTHY FAULT_TOLERANCE STORAGE_SERVERS UNREACHABLE; \
	phase=frozen; degraded=; last=; start=$$(date +%s); \
	for i in $$(seq 300); do \
		cur=$$(st | jq -r '$(FAILOVER_JQ)' 2>/dev/null); \
		if [ -n "$$cur" ]; then \
			[ "$$phase $$cur" != "$$last" ] && { printf '%s\t%s\t%s\n' $$(( $$(date +%s) - start )) $$phase "$$cur"; last="$$phase $$cur"; }; \
			ok=$$(echo "$$cur" | awk '$$1 == "healthy" && $$2 == "true" && $$3 >= 1 {print 1}'); \
			if [ $$phase = frozen ]; then \
				[ -z "$$ok" ] && degraded=1; \
				if [ -n "$$degraded" ] && [ -n "$$ok" ] || [ $$i -ge $(FREEZE_MAX_POLLS) ]; then \
					[ -n "$$degraded" ] && echo "-- FDB re-replicated around the frozen server; resuming it (SIGCONT)" \
						|| echo "-- no degradation after $(FREEZE_MAX_POLLS) polls; resuming it (SIGCONT)"; \
					resume; trap - EXIT; phase=resumed; \
				fi; \
			elif [ -n "$$ok" ] && [ "$$(echo "$$cur" | cut -f4)" = "$$want" ]; then break; fi; \
		fi; \
		[ $$i -lt 300 ] || { echo 'not recovered after 300 polls'; exit 1; }; sleep 2; done
	$(KUBECTL) wait foundationdbcluster/test-cluster --timeout=10m \
		--for=jsonpath='{.status.health.available}'=true
	$(KUBECTL) wait foundationdbcluster/test-cluster --timeout=10m \
		--for=jsonpath='{.status.health.fullReplication}'=true
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec 'status' | grep -E 'Fault Tolerance|Replication health'
	@$(MAKE) --no-print-directory roles

status:
	$(KUBECTL) get foundationdbcluster
	$(KUBECTL) exec $(FDB_POD) -c foundationdb -- fdbcli --exec 'status details'

cli:
	$(KUBECTL) exec -it $(FDB_POD) -c foundationdb -- fdbcli

# curl localhost:9444/metrics while this runs
metrics:
	$(KUBECTL) port-forward svc/fdb-exporter 9444:9444

down:
	kind delete cluster --name $(CLUSTER_NAME)
