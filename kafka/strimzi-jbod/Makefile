CLUSTER_NAME := kafka-strimzi-jbod
NAMESPACE := kafka
KUBECONFIG := $(CURDIR)/results/kubeconfig
HELM_CONFIG_HOME := $(CURDIR)/results/helm/config
HELM_CACHE_HOME := $(CURDIR)/results/helm/cache
HELM_DATA_HOME := $(CURDIR)/results/helm/data
export KUBECONFIG HELM_CONFIG_HOME HELM_CACHE_HOME HELM_DATA_HOME
KUBECTL := kubectl --kubeconfig "$(KUBECONFIG)" --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kubeconfig "$(KUBECONFIG)" --kube-context kind-$(CLUSTER_NAME)
STRIMZI_VERSION := 1.3.1
KAFKA_VERSION := 4.3.2
PRODUCER_IMAGE := kafka-strimzi-producer:dev
CONSUMER_IMAGE := kafka-strimzi-consumer:dev
BENCHMARK_COUNT ?= 2000000
BENCHMARK_RATE ?= 50000
COUNT ?= 1000000
LIVE_RATE ?= 1000
BURST_RATE ?= 0
DURATION_SECONDS ?= 1800
DELIVERY_TIMEOUT_MS ?= 300000
REBALANCE ?= kafka-jbod-rebalance
export COUNT LIVE_RATE BURST_RATE DURATION_SECONDS DELIVERY_TIMEOUT_MS CASE_ID REBALANCE
CONTROL := python3 control.py
CLUSTER := python3 cluster.py
.NOTPARALLEL:
.PHONY: build-images kind-up up namespace operator cluster images test demo chaos-broker chaos-controller chaos-disk test-production benchmark verify-history diagnose-history freeze-failed-history capture-failed-history disk-add disk-rebalance-plan disk-remove-plan disk-approve disk-remove-finish check-controls status logs cli down kind-down
build-images:
	$(CLUSTER) build-images
kind-up: build-images
	$(CLUSTER) kind-up
namespace:
	$(CLUSTER) namespace
operator: namespace
	$(HELM) repo add strimzi https://strimzi.io/charts/ --force-update
	$(HELM) repo update strimzi
	$(HELM) upgrade --install strimzi-operator strimzi/strimzi-kafka-operator \
		--version $(STRIMZI_VERSION) --namespace $(NAMESPACE) --wait --timeout 5m \
		--set 'watchNamespaces={$(NAMESPACE)}'
	$(KUBECTL) rollout status deployment/strimzi-cluster-operator --timeout=5m
cluster:
	$(CLUSTER) configure-kafka
up: kind-up
	$(MAKE) operator
	$(MAKE) cluster
	$(MAKE) images
images: build-images
	$(CLUSTER) assert-owned
	kind load docker-image --name $(CLUSTER_NAME) --nodes $(CLUSTER_NAME)-control-plane $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)
check-controls:
	python3 -m unittest test_controls.py
test: images check-controls
	$(CONTROL) correctness
demo: test
chaos-broker: images check-controls
	$(CONTROL) broker-crash
chaos-controller: images check-controls
	$(CONTROL) controller-crash
chaos-disk: images check-controls
	$(CONTROL) disk-failure
capture-failed-history:
	python3 capture_failed_topic.py
freeze-failed-history: images check-controls
	$(CONTROL) freeze-failed-history
diagnose-history: images check-controls
	$(CONTROL) diagnose-history
verify-history: check-controls
	$(CONTROL) verify-history
benchmark: images check-controls
	@for trial in 1 2 3; do \
		echo "Fresh workload process trial $$trial"; \
		COUNT=$(BENCHMARK_COUNT) LIVE_RATE=$(BENCHMARK_RATE) $(CONTROL) benchmark || exit $$?; \
	done
test-production:
	$(MAKE) test
	$(MAKE) chaos-broker
	$(MAKE) chaos-controller
	$(MAKE) chaos-disk
disk-add:
	$(CLUSTER) assert-owned
	$(KUBECTL) apply -f manifests/kafka-two-disks.yaml
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m
disk-rebalance-plan:
	$(CLUSTER) assert-owned
	$(KUBECTL) delete kafkarebalance $(REBALANCE) --ignore-not-found
	$(KUBECTL) apply -f manifests/rebalance-disks.yaml
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=ProposalReady --timeout=15m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-remove-plan:
	$(CLUSTER) assert-owned
	$(KUBECTL) delete kafkarebalance $(REBALANCE) --ignore-not-found
	$(KUBECTL) apply -f manifests/remove-disk.yaml
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=ProposalReady --timeout=15m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-approve:
	$(CLUSTER) assert-owned
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o jsonpath='{.status.conditions[?(@.type=="ProposalReady")].status}' | grep -qx True
	$(KUBECTL) annotate kafkarebalance $(REBALANCE) strimzi.io/rebalance="approve" --overwrite
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=Ready --timeout=20m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-remove-finish:
	$(CONTROL) remove-added-disks

status:
	$(CLUSTER) assert-owned
	$(KUBECTL) get kafka,kafkanodepool,kafkatopic,kafkarebalance,pods,pvc
logs:
	$(CLUSTER) assert-owned
	$(KUBECTL) logs -l strimzi.io/cluster=kafka -c kafka --tail=100
cli:
	$(CLUSTER) assert-owned
	$(KUBECTL) exec -it kafka-brokers-3 -c kafka -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-kafka-bootstrap:9092 --list
down:
	$(CONTROL) down
kind-down:
	$(CONTROL) kind-down
