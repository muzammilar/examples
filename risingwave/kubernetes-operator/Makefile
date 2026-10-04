CLUSTER_NAME := risingwave-operator
OPERATOR_VERSION := v0.18.0
CERT_MANAGER_VERSION := v1.21.2
RW_IMAGE := risingwavelabs/risingwave:v3.1.0
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
PSQL = $(KUBECTL) exec -i psql -- psql -h risingwave-frontend -p 4567 -U root -d dev -v ON_ERROR_STOP=1 -X

.PHONY: up test status cli down kind-cluster cert-manager operator cluster client

up: kind-cluster cert-manager operator cluster client

# The RisingWave image (2.5 GB compressed, 11.2 GB unpacked) is side-loaded from local Docker
# when it is there (`docker pull $(RW_IMAGE)`), otherwise the node pulls it.
kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	# `kind load docker-image` fails on multi-arch images ("content digest ... not found"):
	# save only the Docker host's platform and import it with ctr
	if docker image inspect $(RW_IMAGE) >/dev/null 2>&1; then \
		docker save --platform linux/$$(docker version -f '{{.Server.Arch}}') $(RW_IMAGE) | \
			docker exec -i $(CLUSTER_NAME)-control-plane ctr -n k8s.io images import --digests -; fi

# the operator's webhooks get their serving certificate from cert-manager
cert-manager:
	$(KUBECTL) apply -f https://github.com/cert-manager/cert-manager/releases/download/$(CERT_MANAGER_VERSION)/cert-manager.yaml
	$(KUBECTL) -n cert-manager rollout status deploy/cert-manager-webhook --timeout=5m

# install manifest from the release (README "Install RisingWave Operator"); retried while the
# cert-manager webhook is still starting
operator:
	for i in $$(seq 1 30); do \
		$(KUBECTL) apply --server-side -f https://github.com/risingwavelabs/risingwave-operator/releases/download/$(OPERATOR_VERSION)/risingwave-operator.yaml && break; \
		sleep 5; done
	$(KUBECTL) -n risingwave-operator-system rollout status deploy/risingwave-operator-controller-manager --timeout=5m

cluster:
	$(KUBECTL) apply -f backends.yaml
	$(KUBECTL) rollout status deploy/postgres deploy/minio --timeout=5m
	for i in $$(seq 1 30); do $(KUBECTL) apply -f risingwave.yaml && break; sleep 5; done
	$(KUBECTL) wait risingwave/risingwave --for=condition=Running --timeout=15m
	$(KUBECTL) get risingwave,pods -o wide

client:
	$(KUBECTL) get pod psql >/dev/null 2>&1 || \
		$(KUBECTL) run psql --image=postgres:17-alpine --restart=Never --command -- sleep infinity
	$(KUBECTL) wait pod/psql --for=condition=Ready --timeout=5m

test:
	$(PSQL) -e < sql/01-mv.sql

status:
	$(KUBECTL) get risingwave,pods,svc -o wide
	$(PSQL) -c "SELECT id, host, type, state, parallelism FROM rw_worker_nodes ORDER BY id"

cli:
	$(KUBECTL) exec -it psql -- psql -h risingwave-frontend -p 4567 -U root -d dev

down:
	kind delete cluster --name $(CLUSTER_NAME)
