CLUSTER_NAME := rondb-helm
NAMESPACE := rondb
RELEASE := my-rondb
CHART_VERSION := 26.2.20
RONDB_IMAGE := docker.io/hopsworks/rondb:26.02.11
HELPER_IMAGES := docker.io/hopsworks/hwutils:1.7 docker.io/library/python:3.12-slim
BENCH_IMAGE := rondb-helm-sysbench:local
KUBECTL := kubectl --context kind-$(CLUSTER_NAME)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
HELM_INSTALL := $(HELM) upgrade --install $(RELEASE) rondb/rondb --version $(CHART_VERSION) \
	--namespace $(NAMESPACE) --values values.yaml
MGM := $(KUBECTL) -n $(NAMESPACE) exec mgmds-0 -c mgmd -- ndb_mgm
# root has a generated password in Secret mysql-passwords
MYSQL = $(KUBECTL) -n $(NAMESPACE) exec -i mysqlds-0 -c mysqld -- mysql -uroot -p"$$($(KUBECTL) -n $(NAMESPACE) \
	get secret mysql-passwords -o jsonpath='{.data.root}' | base64 -d)" --table
FAILOVER_POD := node-group-0-1
LOAD_SECS ?= 120
KILL_AFTER ?= 30
THREADS ?= 8

.PHONY: up kind-cluster images install test failover node-groups status cli down

up: kind-cluster images install

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

# pull once on the host and import into the kind node (the RonDB image is ~1.4 GB unpacked);
# the sysbench client image is built from bench/Dockerfile. `docker save --platform` + ctr
# import instead of `kind load docker-image`, which fails on Docker Desktop's multi-platform
# image store ("ctr: content digest sha256:...: not found")
ARCH := $(shell docker version -f '{{.Server.Arch}}')
images:
	for i in $(RONDB_IMAGE) $(HELPER_IMAGES); do docker image inspect $$i >/dev/null 2>&1 || docker pull -q $$i; done
	docker build -q -t $(BENCH_IMAGE) bench
	for i in $(RONDB_IMAGE) $(HELPER_IMAGES) $(BENCH_IMAGE); do echo "==> $$i"; \
		docker save --platform linux/$(ARCH) $$i | docker exec -i $(CLUSTER_NAME)-control-plane \
		ctr -n k8s.io images import --digests - >/dev/null || exit 1; done

install:
	helm repo add rondb https://logicalclocks.github.io/rondb-helm >/dev/null 2>&1 || true
	helm repo update rondb
	$(KUBECTL) create namespace $(NAMESPACE) --dry-run=client -o yaml | $(KUBECTL) apply -f -
	time $(HELM_INSTALL) --wait --timeout 20m
	$(KUBECTL) -n $(NAMESPACE) get pods -o wide
	$(MGM) -e show

# the chart's own Helm tests (generate-data writes tables through the mysqld Service,
# verify-data reads them back), then a short SQL walkthrough
test:
	$(HELM) test -n $(NAMESPACE) $(RELEASE) --logs --filter name=generate-data
	$(HELM) test -n $(NAMESPACE) $(RELEASE) --logs --filter name=verify-data
	$(MYSQL) < sql/demo.sql

# sysbench oltp_read_write (1.0.20, bench/Dockerfile) through the mysqld Service for LOAD_SECS s;
# KILL_AFTER s in, data node pod FAILOVER_POD is force-deleted (no graceful shutdown). Node 1
# holds a replica of every fragment, so the load keeps going; the StatefulSet recreates the pod
# on its PVC and the node restarts (local recovery + catch-up from node 1). Interval reports
# are printed with the kill / back-to-started times.
failover:
	@mkdir -p results
	$(KUBECTL) -n $(NAMESPACE) delete pod sysbench --ignore-not-found --wait
	@pw=$$($(KUBECTL) -n $(NAMESPACE) get secret mysql-passwords -o jsonpath='{.data.helm}' | base64 -d); \
	SB="sysbench --db-driver=mysql --mysql-host=mysqld --mysql-user=helm --mysql-password=$$pw --mysql-db=sbtest --tables=4 --table-size=50000"; \
	$(MYSQL) -e "CREATE DATABASE IF NOT EXISTS sbtest"; \
	$(KUBECTL) -n $(NAMESPACE) run sysbench --image=docker.io/library/$(BENCH_IMAGE) --image-pull-policy=Never --restart=Never \
		--command -- sh -c "$$SB --mysql-storage-engine=ndbcluster --threads=4 oltp_read_write prepare >/dev/null && \
		echo prepared && $$SB --threads=$(THREADS) --time=$(LOAD_SECS) --report-interval=5 --percentile=99 \
		--mysql-ignore-errors=all oltp_read_write run; $$SB oltp_read_write cleanup >/dev/null"
	@until $(KUBECTL) -n $(NAMESPACE) logs sysbench 2>/dev/null | grep -q '^prepared'; do sleep 2; done
	@echo "$$(date +%s) load start" > results/failover-events.txt
	sleep $(KILL_AFTER)
	@echo "$$(date +%s) kill pod $(FAILOVER_POD)" >> results/failover-events.txt
	$(KUBECTL) -n $(NAMESPACE) delete pod $(FAILOVER_POD) --grace-period=0 --force
	@for i in $$(seq 120); do $(MGM) -e "2 status" 2>/dev/null | grep -q 'not connected' && break; sleep 1; done
	@echo "$$(date +%s) node 2 not connected" >> results/failover-events.txt
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'
	@for i in $$(seq 300); do $(MGM) -e "2 status" 2>/dev/null | grep -q ': started' && break; sleep 2; done
	@echo "$$(date +%s) node 2 started" >> results/failover-events.txt
	$(MGM) -e show | sed -n '/ndbd(NDB)/,/^$$/p'
	$(KUBECTL) -n $(NAMESPACE) wait pod/sysbench --for=jsonpath='{.status.phase}'=Succeeded --timeout=15m
	{ echo "start_epoch=$$(head -n 1 results/failover-events.txt | cut -d' ' -f1)"; \
		$(KUBECTL) -n $(NAMESPACE) logs sysbench; } > results/failover-sysbench.txt
	sh bench/series.sh results/failover-sysbench.txt results/failover-events.txt
	@grep -E 'transactions:|ignored errors:|99th percentile:' results/failover-sysbench.txt
	$(KUBECTL) -n $(NAMESPACE) logs $(FAILOVER_POD) -c ndbmtd | grep -E 'Start phase 101 completed|NODE-START\] completed' | tail -n 2
	$(KUBECTL) -n $(NAMESPACE) delete pod sysbench --wait=false

# more node groups: the chart refuses (expected to fail); see ../online-scaling for RonDB's
# own online CREATE NODEGROUP + REORGANIZE PARTITION on Docker Compose
node-groups:
	-$(HELM_INSTALL) --set clusterSize.numNodeGroups=2

status:
	$(KUBECTL) -n $(NAMESPACE) get pods -o wide
	$(MGM) -e show
	$(MGM) -e 'all report memory'

cli:
	$(KUBECTL) -n $(NAMESPACE) exec -it mysqlds-0 -c mysqld -- sh -c 'mysql -uroot -p"$$MYSQL_ROOT_PASSWORD"'

down:
	kind delete cluster --name $(CLUSTER_NAME)
