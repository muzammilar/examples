.PHONY: help deps fmt vet build run test test-race bench bench-cpu test-all clean docker-build up wait logs down

# Variables
APP_NAME := workerpool
MAIN_PKG := ./cmd/workerpool
BENCH ?= .
BENCH_COUNT ?= 1
BENCH_TIME ?= 1s
# images built (not pulled) by docker-compose.yml; removed by down
BUILT_IMAGES := dynamic-threadpool
PROMETHEUS_URL := http://localhost:19115
GRAFANA_URL := http://localhost:13015
# Grafana base URL polled by `make wait`
GRAFANA_BASE := http://localhost:13015

help: ## Display this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

deps: ## Download Go dependencies
	go mod download
	go mod tidy

fmt: ## Format Go code
	go fmt ./...

vet: ## Run go vet
	go vet ./...

build: ## Build the application
	go build -o bin/$(APP_NAME) $(MAIN_PKG)

run: ## Run the application
	go run $(MAIN_PKG)

test: ## Run unit tests only (benchmarks are skipped)
	go test -v ./...

test-race: ## Run unit tests with the race detector
	go test -race -count=1 ./...

bench: ## Run benchmarks only (filter with BENCH=<regex>, e.g. BENCH=Submit)
	go test -run='^$$' -bench='$(BENCH)' -benchmem -count=$(BENCH_COUNT) -benchtime=$(BENCH_TIME) ./...

bench-cpu: ## Run benchmarks across GOMAXPROCS values 1,4,8
	go test -run='^$$' -bench='$(BENCH)' -benchmem -cpu=1,4,8 ./...

test-all: vet test-race bench ## Run vet, race tests, then benchmarks

clean: ## Clean build artifacts
	rm -rf bin/
	go clean

docker-build: ## Build Docker image
	docker compose build

up: ## Build and start the demo, Prometheus and Grafana in the background
	docker compose up --build --detach
	@$(MAKE) --no-print-directory wait
	@echo ""
	@echo "Prometheus: $(PROMETHEUS_URL)  (targets: $(PROMETHEUS_URL)/targets)"
	@echo "Grafana:    $(GRAFANA_URL)/d/dynamic-threadpool  (anonymous admin)"
	@echo "Logs:       make logs"

wait: ## Wait (up to 4 minutes) until Grafana serves the Prometheus datasource and the dashboard
	@echo "Waiting for Grafana at $(GRAFANA_BASE) ..."
	@for i in $$(seq 1 120); do \
		curl -sf $(GRAFANA_BASE)/api/datasources/uid/prometheus/health >/dev/null && \
		curl -sf $(GRAFANA_BASE)/api/dashboards/uid/dynamic-threadpool >/dev/null && { echo "Grafana is ready"; exit 0; }; \
		sleep 2; \
	done; echo "Grafana was not ready after 4 minutes (check make logs)"; exit 1

logs: ## Follow the demo's logs
	docker compose logs --follow dynamic-threadpool

down: ## Remove this project's containers, networks, volumes, orphans and built images (pulled images are kept)
	docker compose down --volumes --remove-orphans
	-docker image rm $(BUILT_IMAGES)
