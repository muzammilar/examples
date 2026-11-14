CLUSTER_NAME := kafka-cross-cluster-mirroring
NAMESPACE := kafka
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
KIND ?= kind
STRIMZI_VERSION := 1.3.1
SOURCE_ALIAS := east
TARGET_ALIAS := west
MM2_NAME := replication
CHAOS_TARGET_BROKER ?= west-west-brokers-3
PRODUCER_IMAGE := kafka-mirror-producer:dev
CONSUMER_IMAGE := kafka-mirror-consumer:dev
COUNT ?= 2000000
PROGRESS_EVERY ?= 10000
TOPIC ?= orders-$(shell date -u +%Y%m%d%H%M%S)
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
TOPIC := $(TOPIC)
RUN_ID := $(RUN_ID)
TARGET_TOPIC := $(TOPIC)

.PHONY: kind-up kind-cluster namespace operator clusters mirror up images topic test verify-mirror chaos-mm2-worker chaos-broker-disk-loss chaos-target-broker status logs logs-mm2 cli down kind-down

kind-up: kind-cluster

kind-cluster:
	$(KIND) get clusters | grep -qx $(CLUSTER_NAME) || $(KIND) create cluster --config kind-config.yaml

namespace:
	$(KUBECTL) get namespace $(NAMESPACE) >/dev/null 2>&1 || kubectl --context kind-$(CLUSTER_NAME) create namespace $(NAMESPACE)

operator: namespace
	$(HELM) repo add strimzi https://strimzi.io/charts/ >/dev/null 2>&1 || true
	$(HELM) repo update strimzi
	$(HELM) upgrade --install strimzi-operator strimzi/strimzi-kafka-operator \
		--version $(STRIMZI_VERSION) --namespace $(NAMESPACE) --wait --timeout 5m \
		--set 'watchNamespaces={$(NAMESPACE)}'
	$(KUBECTL) rollout status deployment/strimzi-cluster-operator --timeout=5m

clusters: operator
	CLUSTER_NAME=$(SOURCE_ALIAS) envsubst < manifests/kafka-cluster.yaml | $(KUBECTL) apply -f -
	CLUSTER_NAME=$(TARGET_ALIAS) envsubst < manifests/kafka-cluster.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait kafka/$(SOURCE_ALIAS) --for=condition=Ready --timeout=15m
	$(KUBECTL) wait kafka/$(TARGET_ALIAS) --for=condition=Ready --timeout=15m
	$(KUBECTL) wait pods -l strimzi.io/pool-name=$(SOURCE_ALIAS)-brokers --for=condition=Ready --timeout=10m
	$(KUBECTL) wait pods -l strimzi.io/pool-name=$(TARGET_ALIAS)-brokers --for=condition=Ready --timeout=10m

mirror:
	$(KUBECTL) apply -f manifests/mirrormaker2.yaml
	$(KUBECTL) wait kafkamirrormaker2/$(MM2_NAME) --for=condition=Ready --timeout=10m
	$(KUBECTL) wait pods -l strimzi.io/kind=KafkaMirrorMaker2 --for=condition=Ready --timeout=10m

up: kind-up clusters

images:
	docker build -t $(PRODUCER_IMAGE) producer
	docker build -f consumer/Dockerfile -t $(CONSUMER_IMAGE) .
	$(KIND) load docker-image --name $(CLUSTER_NAME) $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)

topic:
	TOPIC=$(TOPIC) envsubst < manifests/topic.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait kafkatopic/$(TOPIC) --for=condition=Ready --timeout=2m

test: images topic
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(PROGRESS_EVERY) BOOTSTRAP_SERVERS=$(SOURCE_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-producer
	$(MAKE) mirror
	$(MAKE) verify-mirror COUNT=$(COUNT) TOPIC=$(TOPIC) RUN_ID=$(RUN_ID)
	-$(KUBECTL) delete kafkamirrormaker2 $(MM2_NAME) --wait=true --timeout=5m
	$(KUBECTL) wait --for=delete pod -l strimzi.io/kind=KafkaMirrorMaker2 --timeout=5m
	-$(KUBECTL) delete job kafka-consumer --ignore-not-found
	-$(KUBECTL) delete job kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID)-west COUNT=$(COUNT) TOPIC=$(TOPIC) GROUP_ID=dr-$(RUN_ID) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-consumer --for=condition=Ready --timeout=2m
	RUN_ID=$(RUN_ID)-west COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(PROGRESS_EVERY) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=30m
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer

verify-mirror:
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) GROUP_ID=dr-$(RUN_ID) BOOTSTRAP_SERVERS=$(SOURCE_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer
	@for i in $$(seq 600); do \
		offsets=$$($(KUBECTL) exec $(TARGET_ALIAS)-$(TARGET_ALIAS)-brokers-3 -c kafka -- /opt/kafka/bin/kafka-consumer-groups.sh --bootstrap-server $(TARGET_ALIAS)-kafka-bootstrap:9092 --describe --group dr-$(RUN_ID) 2>/dev/null || true); \
		echo "$$offsets" | awk -v topic='$(TARGET_TOPIC)' '$$2 == topic && $$4 ~ /^[0-9]+$$/ && $$6 ~ /^[0-9]+$$/ { partitions++; if ($$6 > 1) behind = 1 } END { exit !(partitions == 6 && !behind) }' && { echo 'MirrorMaker 2 synchronized all six consumer partition offsets to the target tail'; exit 0; }; \
		sleep 1; \
	done; echo 'MirrorMaker 2 consumer offset checkpoints did not reach the expected offsets' >&2; exit 1
	-$(KUBECTL) delete job kafka-consumer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TARGET_TOPIC) GROUP_ID=validate-$(RUN_ID) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer

chaos-mm2-worker: mirror images topic
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(PROGRESS_EVERY) BOOTSTRAP_SERVERS=$(SOURCE_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-producer --for=condition=Ready --timeout=2m
	@$(KUBECTL) logs -f job/kafka-producer | grep -m1 'PRODUCER_PROGRESS'
	@pod=$$($(KUBECTL) get pod -l strimzi.io/kind=KafkaMirrorMaker2 -o jsonpath='{.items[0].metadata.name}'); \
	old_uid=$$($(KUBECTL) get pod "$$pod" -o jsonpath='{.metadata.uid}'); \
	echo "Deleting MirrorMaker 2 worker $$pod during source production"; \
	$(KUBECTL) delete pod "$$pod" --wait=true --timeout=2m; \
	for i in $$(seq 300); do \
		ready=$$($(KUBECTL) get pods -l strimzi.io/kind=KafkaMirrorMaker2 -o jsonpath='{range .items[*]}{.status.containerStatuses[0].ready}{"\n"}{end}' | grep -c '^true$$' || true); \
		uids=$$($(KUBECTL) get pods -l strimzi.io/kind=KafkaMirrorMaker2 -o jsonpath='{range .items[*]}{.metadata.uid}{"\n"}{end}'); \
		[ "$$ready" -ge 2 ] && ! echo "$$uids" | grep -q "$$old_uid" && { echo 'MirrorMaker 2 worker recovered'; exit 0; }; \
		sleep 1; \
	done; echo 'MirrorMaker 2 worker did not recover' >&2; exit 1
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-producer
	$(MAKE) verify-mirror COUNT=$(COUNT) TOPIC=$(TOPIC) RUN_ID=$(RUN_ID)

chaos-target-broker: chaos-broker-disk-loss

chaos-broker-disk-loss: mirror images topic
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(PROGRESS_EVERY) BOOTSTRAP_SERVERS=$(SOURCE_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-producer --for=condition=Ready --timeout=2m
	@$(KUBECTL) logs -f job/kafka-producer | grep -m1 'PRODUCER_PROGRESS'
	@old_uid=$$($(KUBECTL) get pod $(CHAOS_TARGET_BROKER) -o jsonpath='{.metadata.uid}'); \
	$(KUBECTL) delete pod $(CHAOS_TARGET_BROKER) --wait=true --timeout=2m; \
	for i in $$(seq 120); do \
		new_uid=$$($(KUBECTL) get pod $(CHAOS_TARGET_BROKER) -o jsonpath='{.metadata.uid}' 2>/dev/null || true); \
		[ -n "$$new_uid" ] && [ "$$new_uid" != "$$old_uid" ] && { echo 'Strimzi replaced the west broker'; exit 0; }; \
		sleep 1; \
	done; echo 'Strimzi did not replace the west broker' >&2; exit 1
	@set -e; \
	$(KUBECTL) wait pod/$(CHAOS_TARGET_BROKER) --for=condition=Ready --timeout=10m & pod_wait=$$!; \
	$(KUBECTL) wait kafka/$(TARGET_ALIAS) --for=condition=Ready --timeout=10m & kafka_wait=$$!; \
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=30m; \
	wait $$pod_wait; \
	wait $$kafka_wait
	$(KUBECTL) logs job/kafka-producer
	$(MAKE) verify-mirror COUNT=$(COUNT) TOPIC=$(TOPIC) RUN_ID=$(RUN_ID)

status:
	$(KUBECTL) get kafka,kafkanodepool,kafkamirrormaker2,pods

logs:
	$(KUBECTL) logs -l strimzi.io/cluster=$(SOURCE_ALIAS) -c kafka --tail=100
	$(KUBECTL) logs -l strimzi.io/cluster=$(TARGET_ALIAS) -c kafka --tail=100

logs-mm2:
	$(KUBECTL) logs -l strimzi.io/kind=KafkaMirrorMaker2 --all-containers=true --tail=200

cli:
	$(KUBECTL) exec -it $(SOURCE_ALIAS)-$(SOURCE_ALIAS)-brokers-3 -c kafka -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server $(SOURCE_ALIAS)-kafka-bootstrap:9092 --list

down:
	-$(HELM) uninstall strimzi-operator --namespace $(NAMESPACE) --wait
	-kubectl --context kind-$(CLUSTER_NAME) delete namespace $(NAMESPACE) --ignore-not-found --wait=true --timeout=5m

kind-down:
	$(KIND) delete cluster --name $(CLUSTER_NAME)
	-docker image rm $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)
