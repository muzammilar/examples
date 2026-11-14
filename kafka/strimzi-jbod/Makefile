CLUSTER_NAME := kafka-strimzi-jbod
NAMESPACE := kafka
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
STRIMZI_VERSION := 1.3.1
KAFKA_VERSION := 4.3.2
PRODUCER_IMAGE := kafka-strimzi-producer:dev
CONSUMER_IMAGE := kafka-strimzi-consumer:dev
COUNT ?= 1000000
CHAOS_BROKER ?= kafka-brokers-3
CHAOS_AFTER_RECORDS ?= $(shell expr $(COUNT) / 2)
CHAOS_PAUSE_SECONDS ?= 30
REBALANCE ?= kafka-jbod-rebalance
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: kind-up kind-cluster up namespace operator cluster images topic-reset demo chaos test disk-add disk-rebalance-plan disk-remove-plan disk-approve disk-remove-finish status cli logs down kind-down

kind-up: kind-cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

up: kind-up
	$(MAKE) namespace
	$(MAKE) operator
	$(MAKE) cluster

namespace:
	$(KUBECTL) get namespace $(NAMESPACE) >/dev/null 2>&1 || kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE)

operator:
	$(HELM) repo add strimzi https://strimzi.io/charts/ >/dev/null 2>&1 || true
	$(HELM) repo update strimzi
	$(HELM) upgrade --install strimzi-operator strimzi/strimzi-kafka-operator \
		--version $(STRIMZI_VERSION) --namespace $(NAMESPACE) --wait --timeout 5m \
		--set 'watchNamespaces={$(NAMESPACE)}'
	$(KUBECTL) rollout status deployment/strimzi-cluster-operator --timeout=5m

cluster:
	$(KUBECTL) apply -f manifests/kafka-one-disk.yaml
	$(KUBECTL) apply -f manifests/topic.yaml
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=15m

images:
	docker build -t $(PRODUCER_IMAGE) producer
	docker build -f consumer/Dockerfile -t $(CONSUMER_IMAGE) .
	kind load docker-image --name $(CLUSTER_NAME) $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)

topic-reset:
	-$(KUBECTL) delete kafkatopic orders --wait=true --timeout=2m
	$(KUBECTL) apply -f manifests/topic.yaml
	$(KUBECTL) wait kafkatopic/orders --for=condition=Ready --timeout=2m

demo: images topic-reset
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) CHAOS_AFTER_RECORDS=0 CHAOS_PAUSE_SECONDS=0 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-producer
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-consumer

chaos: images topic-reset
	@test $(COUNT) -ge 2 && test $(CHAOS_AFTER_RECORDS) -ge 1 && test $(CHAOS_AFTER_RECORDS) -lt $(COUNT) || \
		{ echo 'COUNT must be at least 2 and CHAOS_AFTER_RECORDS must be between 1 and COUNT-1' >&2; exit 1; }
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) CHAOS_AFTER_RECORDS=$(CHAOS_AFTER_RECORDS) CHAOS_PAUSE_SECONDS=$(CHAOS_PAUSE_SECONDS) envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-producer --for=condition=Ready --timeout=2m
	@for i in $$(seq 300); do \
		logs=$$($(KUBECTL) logs job/kafka-producer 2>/dev/null || true); \
		echo "$$logs" | grep -q 'CHAOS_CHECKPOINT' && { echo "$$logs" | grep 'CHAOS_CHECKPOINT' | tail -1; exit 0; }; \
		sleep 1; \
	done; echo 'producer did not reach the chaos checkpoint' >&2; exit 1
	@old_uid=$$($(KUBECTL) get pod $(CHAOS_BROKER) -o jsonpath='{.metadata.uid}'); \
	$(KUBECTL) delete pod $(CHAOS_BROKER) --wait=true --timeout=2m; \
	for i in $$(seq 60); do \
		new_uid=$$($(KUBECTL) get pod $(CHAOS_BROKER) -o jsonpath='{.metadata.uid}' 2>/dev/null || true); \
		[ -n "$$new_uid" ] && [ "$$new_uid" != "$$old_uid" ] && { echo 'Strimzi created a replacement broker pod'; exit 0; }; \
		sleep 1; \
	done; echo 'Strimzi did not replace the broker pod' >&2; exit 1
	@set -e; \
	$(KUBECTL) wait pod/$(CHAOS_BROKER) --for=condition=Ready --timeout=10m & pod_wait=$$!; \
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m & kafka_wait=$$!; \
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=20m; \
	wait $$pod_wait; \
	wait $$kafka_wait
	$(KUBECTL) logs job/kafka-producer
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-consumer

test: demo

disk-add:
	$(KUBECTL) apply -f manifests/kafka-two-disks.yaml
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m

disk-rebalance-plan:
	$(KUBECTL) delete kafkarebalance $(REBALANCE) --ignore-not-found
	$(KUBECTL) apply -f manifests/rebalance-disks.yaml
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=ProposalReady --timeout=15m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-remove-plan:
	$(KUBECTL) delete kafkarebalance $(REBALANCE) --ignore-not-found
	$(KUBECTL) apply -f manifests/remove-disk.yaml
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=ProposalReady --timeout=15m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-approve:
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o jsonpath='{.status.conditions[?(@.type=="ProposalReady")].status}' | grep -qx True
	$(KUBECTL) annotate kafkarebalance $(REBALANCE) strimzi.io/rebalance="approve" --overwrite
	$(KUBECTL) wait kafkarebalance/$(REBALANCE) --for=condition=Ready --timeout=20m
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o yaml

disk-remove-finish:
	$(KUBECTL) get kafkarebalance $(REBALANCE) -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' | grep -qx True
	$(KUBECTL) apply -f manifests/kafka-one-disk.yaml
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m

status:
	$(KUBECTL) get kafka,kafkanodepool,kafkatopic,kafkarebalance,pods,pvc

logs:
	$(KUBECTL) logs -l strimzi.io/cluster=kafka -c kafka --tail=100

cli:
	$(KUBECTL) exec -it kafka-brokers-3 -c kafka -- /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-kafka-bootstrap:9092 --list

down:
	-kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE) delete kafkarebalance --all --ignore-not-found --wait=true --timeout=2m
	-kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE) delete kafkatopic --all --ignore-not-found --wait=true --timeout=2m
	-kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE) delete kafka kafka --ignore-not-found --wait=true --timeout=15m
	-$(HELM) uninstall strimzi-operator --namespace $(NAMESPACE) --wait
	-kubectl --context kind-$(CLUSTER_NAME) delete namespace $(NAMESPACE) --ignore-not-found --wait=true --timeout=3m

kind-down:
	kind delete cluster --name $(CLUSTER_NAME)
	-docker image rm $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)
