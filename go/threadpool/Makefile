.PHONY: help deps fmt vet build run run-continuous test test-race bench bench-cpu test-all clean docker-build docker-up docker-logs docker-down

# Variables
APP_NAME := threadpool
MAIN_PKG := .
BENCH ?= .
BENCH_COUNT ?= 1
BENCH_TIME ?= 1s
RATE ?= 1000
# Images built (not pulled) by docker-compose.yml; docker-down removes only these.
BUILT_IMAGES := threadpool
GRAFANA_URL := http://localhost:13013/d/threadpool-static
PROMETHEUS_URL := http://localhost:19113

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

run: ## Run the application (one-shot: process -messages and exit)
	go run $(MAIN_PKG)

run-continuous: ## Run continuously at RATE msgs/sec with metrics on :8080 until Ctrl-C
	go run $(MAIN_PKG) -rate $(RATE) -duration 0 -task-time 5ms -metrics-addr :8080

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

docker-up: ## Build and start the app, Prometheus and Grafana in the background
	docker compose up --build --detach
	@echo "Grafana:    $(GRAFANA_URL) (anonymous admin)"
	@echo "Prometheus: $(PROMETHEUS_URL)"

docker-logs: ## Follow the app logs
	docker compose logs -f threadpool

docker-down: ## Stop services; remove containers, networks, volumes, orphans and only the built images
	docker compose down --volumes --remove-orphans
	-docker image rm $(BUILT_IMAGES)
