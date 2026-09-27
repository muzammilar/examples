.PHONY: help deps fmt vet build run-admin run-producer run-consumer test test-race bench bench-cpu test-all test-integration bench-integration clean docker-build docker-up docker-down

# Variables
BIN_DIR := bin
BENCH ?= .
BENCH_COUNT ?= 1
BENCH_TIME ?= 1s
INTEGRATION_BENCH ?= Integration
# The compose file lives in the parent directory (the go module is in `trees`)
COMPOSE := docker compose --project-directory .. -f ../docker-compose.yml
# Kafka brokers exposed on the host by the docker compose setup (override for another cluster)
KAFKA_BROKERS ?= localhost:29092,localhost:39092,localhost:49092
export KAFKA_BROKERS

help: ## Display this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

deps: ## Download Go dependencies
	go mod download
	go mod tidy

fmt: ## Format Go code
	go fmt ./...

vet: ## Run go vet (including the integration tests)
	go vet ./...
	go vet -tags integration ./...

build: ## Build the admin, producer and consumer binaries into bin/
	go build -o $(BIN_DIR)/ ./admin ./producer ./consumer

run-admin: ## Create the topics on the compose kafka (from the host)
	go run ./admin -brokers $(KAFKA_BROKERS) -topics trees,test

run-producer: ## Run a producer against the compose kafka (from the host, metrics on :8081)
	go run ./producer -brokers $(KAFKA_BROKERS) -topic trees -metrics.addr :8081 -log.level debug

run-consumer: ## Run a consumer group member against the compose kafka (from the host, metrics on :8082)
	go run ./consumer -brokers $(KAFKA_BROKERS) -topics trees,test -metrics.addr :8082 -log.level debug

test: ## Run unit tests only (no broker needed, benchmarks are skipped)
	go test -v ./...

test-race: ## Run unit tests with the race detector
	go test -race -count=1 ./...

bench: ## Run benchmarks only, no broker needed (filter with BENCH=<regex>, e.g. BENCH=Decode)
	go test -run='^$$' -bench='$(BENCH)' -benchmem -count=$(BENCH_COUNT) -benchtime=$(BENCH_TIME) ./...

bench-cpu: ## Run benchmarks across GOMAXPROCS values 1,4,8
	go test -run='^$$' -bench='$(BENCH)' -benchmem -cpu=1,4,8 ./...

test-all: vet test-race bench ## Run vet, race tests, then benchmarks

test-integration: ## Run unit + integration tests against kafka (requires `make docker-up`)
	go test -tags integration -race -count=1 -run . ./...

bench-integration: ## Run the produce benchmarks against kafka (requires `make docker-up`)
	go test -tags integration -run='^$$' -bench='$(INTEGRATION_BENCH)' -benchmem -count=$(BENCH_COUNT) -benchtime=$(BENCH_TIME) ./...

clean: ## Clean build artifacts
	rm -rf $(BIN_DIR)/
	go clean

docker-build: ## Build the Docker images
	$(COMPOSE) build

docker-up: ## Start kafka, the admin, producers, consumers and prometheus (detached)
	$(COMPOSE) up --build --detach

docker-down: ## Stop services and remove the locally built images and volumes
	$(COMPOSE) down --rmi local --volumes
