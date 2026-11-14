CLUSTER_NAME := kafka-strimzi-jbod
NAMESPACE := kafka
KUBECTL := kubectl --context kind-$(CLUSTER_NAME) -n $(NAMESPACE)
HELM := helm --kube-context kind-$(CLUSTER_NAME)
STRIMZI_VERSION := 1.3.1
KAFKA_VERSION := 4.3.2
PRODUCER_IMAGE := kafka-strimzi-producer:dev
CONSUMER_IMAGE := kafka-strimzi-consumer:dev
COUNT ?= 1000000
TOPIC ?= orders
CHAOS_BROKER ?= kafka-brokers-3
CHAOS_BROKER_ID ?= 3
CHAOS_VOLUME_ID ?= 1
CHAOS_PROGRESS_RECORDS ?= 10000
CHAOS_TOPIC ?= disk-chaos-orders
REBALANCE ?= kafka-jbod-rebalance
RUN_ID ?= $(shell date -u +%Y%m%dT%H%M%SZ)

.PHONY: kind-up kind-cluster up namespace operator cluster images topic-reset demo chaos-broker chaos-disk test disk-add disk-rebalance-plan disk-remove-plan disk-approve disk-remove-finish status cli logs down kind-down

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
	-$(KUBECTL) delete kafkatopic $(TOPIC) --wait=true --timeout=2m
	TOPIC=$(TOPIC) envsubst < manifests/topic.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait kafkatopic/$(TOPIC) --for=condition=Ready --timeout=2m

demo: images topic-reset
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(CHAOS_PROGRESS_RECORDS) envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-producer
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-consumer

chaos-broker: images topic-reset
	@test $(COUNT) -ge $(CHAOS_PROGRESS_RECORDS) || { echo 'COUNT must be at least CHAOS_PROGRESS_RECORDS' >&2; exit 1; }
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) PROGRESS_EVERY=$(CHAOS_PROGRESS_RECORDS) envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-producer --for=condition=Ready --timeout=2m
	@$(KUBECTL) logs -f job/kafka-producer | grep -m1 'PRODUCER_PROGRESS'
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
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(TOPIC) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-consumer

chaos-disk: images disk-add
	@test $(COUNT) -ge $(CHAOS_PROGRESS_RECORDS) || { echo 'COUNT must be at least CHAOS_PROGRESS_RECORDS' >&2; exit 1; }
	$(MAKE) topic-reset TOPIC=$(CHAOS_TOPIC)
	$(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka-kafka-bootstrap:9092 --describe --topic $(CHAOS_TOPIC) | \
	awk -v topic='$(CHAOS_TOPIC)' -v broker_id='$(CHAOS_BROKER_ID)' -v target_directory='/var/lib/kafka/data-$(CHAOS_VOLUME_ID)/kafka-log$(CHAOS_BROKER_ID)' -f scripts/disk-reassignment.awk | \
	$(KUBECTL) exec -i $(CHAOS_BROKER) -c kafka -- sh -ec 'cat > /tmp/disk-failure-reassignment.json'
	$(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- grep -q '"topic":"$(CHAOS_TOPIC)"' /tmp/disk-failure-reassignment.json
	$(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- /opt/kafka/bin/kafka-reassign-partitions.sh --bootstrap-server kafka-kafka-bootstrap:9092 --execute --reassignment-json-file /tmp/disk-failure-reassignment.json
	@for i in $$(seq 120); do \
		result=$$($(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- /opt/kafka/bin/kafka-reassign-partitions.sh --bootstrap-server kafka-kafka-bootstrap:9092 --verify --reassignment-json-file /tmp/disk-failure-reassignment.json 2>&1 || true); \
		echo "$$result" | grep -q 'is complete' && { echo 'Replica moved to the selected JBOD volume'; break; }; \
		test $$i -lt 120 || { echo "$$result" >&2; echo 'Replica did not move to the selected JBOD volume' >&2; exit 1; }; sleep 1; \
	done
	-$(KUBECTL) delete job kafka-consumer kafka-producer --ignore-not-found
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(CHAOS_TOPIC) PROGRESS_EVERY=$(CHAOS_PROGRESS_RECORDS) envsubst < jobs/producer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait pod -l app=kafka-producer --for=condition=Ready --timeout=2m
	@$(KUBECTL) logs -f job/kafka-producer | grep -m1 'PRODUCER_PROGRESS'
	$(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- sh -ec 'test -d /var/lib/kafka/data-$(CHAOS_VOLUME_ID)/kafka-log$(CHAOS_BROKER_ID); find /var/lib/kafka/data-$(CHAOS_VOLUME_ID)/kafka-log$(CHAOS_BROKER_ID) -maxdepth 1 -type d -name "$(CHAOS_TOPIC)-*" | grep -q .; chmod -R a-w /var/lib/kafka/data-$(CHAOS_VOLUME_ID)/kafka-log$(CHAOS_BROKER_ID)'
	@for i in $$(seq 120); do \
		logs=$$($(KUBECTL) logs $(CHAOS_BROKER) -c kafka --since=3m 2>/dev/null || true); \
		echo "$$logs" | grep -Eqi 'LogDirFailureHandler|LogDirFailureChannel|offline log directory|KafkaStorageException' && { echo 'Kafka observed the injected disk write failure'; echo "$$logs" | grep -Ei 'LogDirFailureHandler|LogDirFailureChannel|offline log directory|KafkaStorageException' | tail -5; exit 0; }; \
		sleep 1; \
	done; echo 'Kafka did not report the injected disk write failure' >&2; exit 1
	$(KUBECTL) wait job/kafka-producer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-producer
	$(KUBECTL) exec $(CHAOS_BROKER) -c kafka -- sh -ec 'chmod -R u+w /var/lib/kafka/data-$(CHAOS_VOLUME_ID)/kafka-log$(CHAOS_BROKER_ID)'
	-$(KUBECTL) delete pod $(CHAOS_BROKER) --wait=true --timeout=2m
	$(KUBECTL) wait pod/$(CHAOS_BROKER) --for=condition=Ready --timeout=10m
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m
	RUN_ID=$(RUN_ID) COUNT=$(COUNT) TOPIC=$(CHAOS_TOPIC) envsubst < jobs/consumer.yaml | $(KUBECTL) apply -f -
	$(KUBECTL) wait job/kafka-consumer --for=condition=complete --timeout=20m
	$(KUBECTL) logs job/kafka-consumer

test: demo

disk-add:
	$(KUBECTL) apply -f manifests/kafka-two-disks.yaml
	$(KUBECTL) wait kafka/kafka --for=condition=Ready --timeout=10m
	$(KUBECTL) wait pods -l strimzi.io/pool-name=brokers --for=condition=Ready --timeout=10m

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
