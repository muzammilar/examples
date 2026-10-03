CLUSTER_NAME := oceanbase-operator
NAMESPACE := oceanbase
OPERATOR_VERSION := 2.3.4
CERT_MANAGER_VERSION := v1.21.2
OB_IMAGE := oceanbase/oceanbase-cloud-native:4.4.2.1-101000022026050611
CLIENT_IMAGE := mariadb:11.4
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
# ob-operator's resource floors (internal/const/oceanbase/resource.go: 8Gi memory, 30Gi data,
# 30Gi redo log, 10Gi log storage) are read from its config, so they can be lowered with
# environment variables on the manager (viper prefix OB_OPERATOR_, key resource.min*)
MIN_MEMORY ?= 4Gi
MIN_DATA ?= 12Gi
MIN_REDO ?= 12Gi
MIN_LOG ?= 2Gi
# SQL client pod (mariadb client; the observer image has no obclient). --skip-ssl: the
# 11.x client requires TLS by default ("SSL is required, but the server does not support it")
SQL = $(KUBECTL) -n $(NAMESPACE) exec -i sql-client -- mariadb --skip-ssl -h obcluster-standalone-svc -P 2881 --table

.PHONY: up test status cli cli-sys down kind-cluster cert-manager operator cluster tenant client

up: kind-cluster cert-manager operator cluster tenant client

# The observer image (1.7 GB) is side-loaded from local Docker when it is there
# (`docker pull $(OB_IMAGE)`), instead of letting the worker pull it.
kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml
	# `kind load docker-image` fails on this multi-arch image with "ctr: content digest ...
	# not found" (it imports --all-platforms), so save only the Docker host's platform
	if docker image inspect $(OB_IMAGE) >/dev/null 2>&1; then \
		for n in $$(kind get nodes --name $(CLUSTER_NAME) | grep worker); do \
			docker save --platform linux/$$(docker version -f '{{.Server.Arch}}') $(OB_IMAGE) | \
				docker exec -i $$n ctr -n k8s.io images import --digests -; done; fi
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -

# ob-operator's webhooks get their serving certificate from cert-manager
cert-manager:
	$(KUBECTL) apply -f https://github.com/cert-manager/cert-manager/releases/download/$(CERT_MANAGER_VERSION)/cert-manager.yaml
	$(KUBECTL) -n cert-manager rollout status deploy/cert-manager-webhook --timeout=5m

# The chart has no values for the manager's environment, so the floors are set afterwards
# with `kubectl set env` (rolls the manager pod once).
operator:
	helm repo add ob-operator https://oceanbase.github.io/ob-operator/ >/dev/null 2>&1 || true
	helm repo update ob-operator
	$(HELM) upgrade --install ob-operator ob-operator/ob-operator --version $(OPERATOR_VERSION) \
		--namespace oceanbase-system --create-namespace --wait --timeout 10m
	$(KUBECTL) -n oceanbase-system set env deploy/oceanbase-controller-manager \
		OB_OPERATOR_RESOURCE_MINMEMORYSIZE=$(MIN_MEMORY) OB_OPERATOR_RESOURCE_MINDATADISKSIZE=$(MIN_DATA) \
		OB_OPERATOR_RESOURCE_MINREDOLOGDISKSIZE=$(MIN_REDO) OB_OPERATOR_RESOURCE_MINLOGDISKSIZE=$(MIN_LOG)
	$(KUBECTL) -n oceanbase-system rollout status deploy/oceanbase-controller-manager --timeout=5m
	# the webhooks answer a few seconds after the new manager pod is ready (before that:
	# failed calling webhook "mobcluster.kb.io" ... context deadline exceeded); wait for them
	for i in $$(seq 1 30); do $(KUBECTL) -n $(NAMESPACE) apply --dry-run=server -f secrets.yaml -f obcluster.yaml >/dev/null 2>&1 && break; sleep 2; done

# bootstrap takes a few minutes; the OBCluster goes `new` -> ... -> `running`
cluster:
	$(KUBECTL) -n $(NAMESPACE) apply -f secrets.yaml
	$(KUBECTL) -n $(NAMESPACE) apply -f obcluster.yaml
	$(KUBECTL) -n $(NAMESPACE) wait obcluster/obcluster --for=jsonpath='{.status.status}'=running --timeout=20m
	$(KUBECTL) -n $(NAMESPACE) get obcluster,obzone,observer,pods,pvc -o wide

tenant:
	$(KUBECTL) -n $(NAMESPACE) apply -f obtenant.yaml
	$(KUBECTL) -n $(NAMESPACE) wait obtenant/test --for=jsonpath='{.status.status}'=running --timeout=15m
	$(KUBECTL) -n $(NAMESPACE) get obtenant

client:
	$(KUBECTL) -n $(NAMESPACE) get pod sql-client >/dev/null 2>&1 || \
		$(KUBECTL) -n $(NAMESPACE) run sql-client --image=$(CLIENT_IMAGE) --restart=Never --command -- sleep infinity
	$(KUBECTL) -n $(NAMESPACE) wait pod/sql-client --for=condition=Ready --timeout=5m

# sql/*.sql as root@test (the user tenant); *.sys.sql as root@sys
test:
	for f in sql/*.sql; do \
		case $$f in *.sys.sql) u=root@sys p=$$($(KUBECTL) -n $(NAMESPACE) get secret root-password -o jsonpath='{.data.password}' | base64 -d) ;; \
			*) u=root@test p=$$($(KUBECTL) -n $(NAMESPACE) get secret test-root-password -o jsonpath='{.data.password}' | base64 -d) ;; esac; \
		echo; echo "==== $$f (as $$u)"; cat $$f; echo "-- output:"; \
		$(SQL) -u$$u -p$$p < $$f || exit 1; \
	done

status:
	$(KUBECTL) -n $(NAMESPACE) get obcluster,obzone,observer,obtenant,pods,pvc -o wide

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it sql-client -- mariadb --skip-ssl -h obcluster-standalone-svc -P 2881 -uroot@test \
		-p$$($(KUBECTL) -n $(NAMESPACE) get secret test-root-password -o jsonpath='{.data.password}' | base64 -d)

cli-sys:
	$(KUBECTL) -n $(NAMESPACE) exec -it sql-client -- mariadb --skip-ssl -h obcluster-standalone-svc -P 2881 -uroot@sys oceanbase \
		-p$$($(KUBECTL) -n $(NAMESPACE) get secret root-password -o jsonpath='{.data.password}' | base64 -d)

down:
	kind delete cluster --name $(CLUSTER_NAME)
