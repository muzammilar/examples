CLUSTER_NAME := kafka-cross-cluster-mirroring
KUBECONFIG := $(CURDIR)/results/kubeconfig
export KUBECONFIG
NAMESPACE := kafka
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
KIND ?= kind
STRIMZI_VERSION := 1.3.1
SOURCE_ALIAS := east
TARGET_ALIAS := west
MM2_NAME := replication
PRODUCER_IMAGE := kafka-mirror-producer:dev
CONSUMER_IMAGE := kafka-mirror-consumer:dev
COUNT ?= 2000000
PROGRESS_EVERY ?= 10000
LIVE_RATE ?= 1000
MM2_REPLICAS ?= 2
MM2_TASKS_MAX ?= 3
MM2_REBALANCE_MAX_DELAY_MS ?= 300000
MM2_REPLICATION_POLICY ?= identity
export MM2_REPLICATION_POLICY MM2_REPLICAS MM2_TASKS_MAX
MM2_OFFSET_SYNC_LOCATION ?= source
MM2_TOPIC_REFRESH_INTERVAL_SECONDS ?= 5
MM2_GROUP_REFRESH_INTERVAL_SECONDS ?= 5
MM2_CHECKPOINT_INTERVAL_SECONDS ?= 5
MM2_GROUP_OFFSET_SYNC_INTERVAL_SECONDS ?= 5
MM2_TOPIC_CONFIG_SYNC_INTERVAL_SECONDS ?= 5
TOPIC ?= orders-$(shell date -u +%Y%m%d%H%M%S)
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)
TOPIC := $(TOPIC)
RUN_ID := $(RUN_ID)
export COUNT RUN_ID TOPIC
ifeq ($(MM2_REPLICATION_POLICY),identity)
MM2_REPLICATION_POLICY_CLASS := org.apache.kafka.connect.mirror.IdentityReplicationPolicy
TARGET_TOPIC := $(TOPIC)
else ifeq ($(MM2_REPLICATION_POLICY),default)
MM2_REPLICATION_POLICY_CLASS := org.apache.kafka.connect.mirror.DefaultReplicationPolicy
TARGET_TOPIC := $(SOURCE_ALIAS).$(TOPIC)
else
$(error MM2_REPLICATION_POLICY must be identity or default)
endif

.NOTPARALLEL:
.PHONY: build-replication-images replication-images test-live-mirror test-production check-controls checkpoint-proof kind-up kind-cluster namespace operator clusters mirror up images topic test test-cutover verify-retained verify-mirror chaos-mm2-worker chaos-broker-disk-loss chaos-target-broker status logs logs-mm2 cli down kind-down

kind-up: kind-cluster

kind-cluster:
	python3 replication_control.py kind-up

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
	MM2_REPLICAS=$(MM2_REPLICAS) MM2_TASKS_MAX=$(MM2_TASKS_MAX) \
	MM2_REBALANCE_MAX_DELAY_MS=$(MM2_REBALANCE_MAX_DELAY_MS) \
	MM2_REPLICATION_POLICY_CLASS=$(MM2_REPLICATION_POLICY_CLASS) \
	MM2_OFFSET_SYNC_LOCATION=$(MM2_OFFSET_SYNC_LOCATION) \
	MM2_TOPIC_REFRESH_INTERVAL_SECONDS=$(MM2_TOPIC_REFRESH_INTERVAL_SECONDS) \
	MM2_GROUP_REFRESH_INTERVAL_SECONDS=$(MM2_GROUP_REFRESH_INTERVAL_SECONDS) \
	MM2_CHECKPOINT_INTERVAL_SECONDS=$(MM2_CHECKPOINT_INTERVAL_SECONDS) \
	MM2_GROUP_OFFSET_SYNC_INTERVAL_SECONDS=$(MM2_GROUP_OFFSET_SYNC_INTERVAL_SECONDS) \
	MM2_TOPIC_CONFIG_SYNC_INTERVAL_SECONDS=$(MM2_TOPIC_CONFIG_SYNC_INTERVAL_SECONDS) \
	envsubst < manifests/mirrormaker2.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait kafkamirrormaker2/$(MM2_NAME) --for=condition=Ready --timeout=10m
	$(KUBECTL) wait pods -l strimzi.io/kind=KafkaMirrorMaker2 --for=condition=Ready --timeout=10m

up: kind-up clusters

images:
	docker build --label lab.example.system=$(CLUSTER_NAME) -t $(PRODUCER_IMAGE) producer
	docker build --label lab.example.system=$(CLUSTER_NAME) -f consumer/Dockerfile -t $(CONSUMER_IMAGE) .
	python3 replication_control.py record-images $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)
	$(KIND) load docker-image --name $(CLUSTER_NAME) $(PRODUCER_IMAGE) $(CONSUMER_IMAGE)

topic:
	python3 replication_control.py validate-topic
	TOPIC=$(TOPIC) envsubst < manifests/topic.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait kafkatopic/$(TOPIC) --for=condition=Ready --timeout=2m

test:
	python3 replication_control.py cutover

test-cutover: images topic
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
	RUN_ID=$(RUN_ID)-west COUNT=$(COUNT) TOPIC=$(TARGET_TOPIC) GROUP_ID=dr-$(RUN_ID) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-consumer --for=condition=Ready --timeout=2m
	RUN_ID=$(RUN_ID)-west COUNT=$(COUNT) TOPIC=$(TARGET_TOPIC) PROGRESS_EVERY=$(PROGRESS_EVERY) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=30m
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer

verify-retained:
	python3 replication_control.py verify-retained

verify-mirror:
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) GROUP_ID=dr-$(RUN_ID) BOOTSTRAP_SERVERS=$(SOURCE_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer
	$(MAKE) checkpoint-proof TOPIC=$(TOPIC) RUN_ID=$(RUN_ID)
	-$(KUBECTL) delete job kafka-consumer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TARGET_TOPIC) GROUP_ID=validate-$(RUN_ID) BOOTSTRAP_SERVERS=$(TARGET_ALIAS)-kafka-bootstrap:9092 envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=30m
	$(KUBECTL) logs job/kafka-consumer

check-controls:
	PYTHONPYCACHEPREFIX=$(CURDIR)/results/pycache python3 -m unittest test_checkpoint_probe.py test_replication.py

checkpoint-proof: images check-controls
	python3 checkpoint_probe.py --topic "$(TOPIC)" --target-topic "$(TARGET_TOPIC)" --group "dr-$(RUN_ID)" --image "$(CONSUMER_IMAGE)"

build-replication-images:
	python3 prepare_replication.py
	docker build --label lab.example.system=kafka-cross-cluster-mirroring -t kafka-mirror-witness-producer:dev .build/replication/producer
	docker build -f replication-observer/Dockerfile -t kafka-mirror-replication-observer:dev .build/replication
	python3 replication_control.py record-images kafka-mirror-witness-producer:dev kafka-mirror-replication-observer:dev

replication-images: build-replication-images kind-up
	$(KIND) load docker-image --name $(CLUSTER_NAME) --nodes $(CLUSTER_NAME)-control-plane kafka-mirror-witness-producer:dev kafka-mirror-replication-observer:dev

test-live-mirror: replication-images
	COUNT=$(COUNT) LIVE_RATE=$(LIVE_RATE) python3 replication_control.py correctness

chaos-mm2-worker: replication-images
	COUNT=$(COUNT) LIVE_RATE=$(LIVE_RATE) python3 replication_control.py mm2-worker-crash

chaos-target-broker: replication-images
	COUNT=$(COUNT) LIVE_RATE=$(LIVE_RATE) python3 replication_control.py west-broker-crash

chaos-broker-disk-loss: replication-images
	COUNT=$(COUNT) LIVE_RATE=$(LIVE_RATE) python3 replication_control.py west-empty-disk

test-production: replication-images
	COUNT=$(COUNT) LIVE_RATE=$(LIVE_RATE) python3 replication_control.py test-production

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
	python3 replication_control.py down

kind-down:
	python3 replication_control.py kind-down
