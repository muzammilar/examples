CLUSTER_NAME := kafka-strimzi-jbod
NAMESPACE := kafka
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
STRIMZI_VERSION := 1.3.1
KAFKA_VERSION := 4.3.2
PRODUCER_IMAGE := kafka-strimzi-producer:dev
CONSUMER_IMAGE := kafka-strimzi-consumer:dev
COUNT ?= 100
REBALANCE ?= kafka-jbod-rebalance
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: up kind-cluster namespace operator cluster images demo test disk-add disk-rebalance-plan disk-remove-plan disk-approve disk-remove-finish status cli logs down

up: kind-cluster namespace operator cluster

kind-cluster:
	kind get clusters | grep -qx $(CLUSTER_NAME) || kind create cluster --config kind-config.yaml

namespace:
	$(KUBECTL) get namespace $(NAMESPACE) >/dev/null 2>&1 || kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE)

operator:
	helm repo add strimzi https://strimzi.io/charts/ >/dev/null 2>&1 || true
	helm repo update strimzi
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

demo: images
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE) wait pod -l app=kafka-consumer --for=condition=Ready --timeout=2m
	@for i in $$(seq 60); do \
		state=$$(kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE) exec kafka-kafka-0 -c kafka -- \
			/opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server kafka-kafka-bootstrap:9092 \
			--describe --group kafka-jbod-$(RUN_ID) --state 2>/dev/null | awk 'NR > 1 {print $$2; exit}'); \
		[ "$$state" = "Stable" ] && { echo "consumer group is assigned"; exit 0; }; \
		sleep 2; \
	done; echo "consumer group did not become stable"; exit 1
	COUNT=$(COUNT) envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=3m
	$(KUBECTL) logs job/kafka-producer
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=3m
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
	$(KUBECTL) exec -it kafka-kafka-0 -c kafka -- /opt/kafka/bin/kafka-topics.sh \
		--bootstrap-server kafka-kafka-bootstrap:9092 --list

down:
	kind delete cluster --name $(CLUSTER_NAME)
	-docker image rm $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)
