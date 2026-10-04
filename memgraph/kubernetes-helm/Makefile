.PHONY: up test status cli failover logs down kind-cluster image chart

SHELL := bash
.SHELLFLAGS := -o pipefail -ec

CLUSTER_NAME := memgraph-helm
NAMESPACE := memgraph
CHART_VERSION := 1.0.7
CLIENT_IMAGE := memgraph-helm-client:local
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
MG := $(KUBECTL) exec -i memgraph-0 -c memgraph -- mgconsole

up: kind-cluster image chart

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE) --dry-run=client -o yaml \
		| kubectl --context kind-$(CLUSTER_NAME) apply -f -

# Go client (client/) for `make failover`, side-loaded into the kind node (imagePullPolicy: Never)
image:
	docker build -q -t $(CLIENT_IMAGE) client
	kind load docker-image --name $(CLUSTER_NAME) $(CLIENT_IMAGE)

chart:
	helm repo add memgraph https://memgraph.github.io/helm-charts >/dev/null 2>&1 || true
	helm repo update memgraph >/dev/null
	$(HELM) upgrade --install memgraph memgraph/memgraph --version $(CHART_VERSION) \
		-f values.yaml --wait --timeout 10m
	$(KUBECTL) wait pod/memgraph-0 --for=condition=Ready --timeout=300s
	@$(MAKE) --no-print-directory status

test:
	@echo "-- cypher/01-walkthrough.cypher in memgraph-0"; $(MG) --output-format=tabular < cypher/01-walkthrough.cypher
	@echo "-- through the Service (memgraph:7687) from a client pod"
	$(KUBECTL) run mg-test --rm -i --restart=Never --image=docker.io/memgraph/memgraph:3.13.1 \
		--command -- sh -c "echo 'MATCH (a:Account) RETURN count(a) AS accounts;' | mgconsole --host memgraph --output-format=tabular"

status:
	$(KUBECTL) get pods,svc,pvc -o wide
	@echo "SHOW VERSION; SHOW REPLICATION ROLE;" | $(MG) --output-format=csv

cli:
	$(KUBECTL) exec -it memgraph-0 -c memgraph -- mgconsole

# write load (Job), force-delete the only pod at KILL_AFTER s: Memgraph is down until the
# StatefulSet recreates the pod on the same PVC and it recovers from snapshot + WAL. Every
# acknowledged write must still be there (client verify, in the same Job).
DURATION ?= 40
KILL_AFTER ?= 10
failover:
	@$(KUBECTL) delete job load --ignore-not-found >/dev/null
	@echo "MATCH (t:Tick) DETACH DELETE t;" | $(MG) >/dev/null
	@sed 's/{ name: DURATION, value: "40" }/{ name: DURATION, value: "$(DURATION)" }/' jobs/load.yaml | $(KUBECTL) apply -f - >/dev/null
	@$(KUBECTL) wait job/load --for=jsonpath='{.status.active}'=1 --timeout=120s >/dev/null
	@sleep $(KILL_AFTER); uid=$$($(KUBECTL) get pod memgraph-0 -o jsonpath='{.metadata.uid}'); \
	t0=$$(date +%s.%N); echo "== kubectl delete pod memgraph-0 --force --grace-period=0"; \
	$(KUBECTL) delete pod memgraph-0 --force --grace-period=0 2>&1 | grep -v Warning; \
	until u=$$($(KUBECTL) get pod memgraph-0 -o jsonpath='{.metadata.uid}' 2>/dev/null); [ -n "$$u" ] && [ "$$u" != "$$uid" ]; do sleep 0.5; done; \
	$(KUBECTL) wait pod/memgraph-0 --for=condition=Ready --timeout=300s >/dev/null; t1=$$(date +%s.%N); \
	echo "new pod Ready after $$(awk "BEGIN{printf \"%.1f\", $$t1-$$t0}") s"
	@until [ -n "$$($(KUBECTL) get job load -o jsonpath='{.status.succeeded}{.status.failed}')" ]; do sleep 2; done
	@mkdir -p results; $(KUBECTL) logs job/load | tee results/failover.txt | grep -v '^t=' ; \
	echo "writes per second:"; grep '^t=' results/failover.txt | sed -E 's/^t=([0-9]+s) writes\/s=([0-9]+).*/\1:\2/' | paste -sd' ' - | fold -w 150
	@[ "$$($(KUBECTL) get job load -o jsonpath='{.status.succeeded}')" = 1 ]

logs:
	$(KUBECTL) logs -f memgraph-0 -c memgraph

down:
	kind delete cluster --name $(CLUSTER_NAME)
	docker image rm $(CLIENT_IMAGE) 2>/dev/null || true
