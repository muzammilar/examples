.PHONY: help generate deps fmt vet build run test test-race bench bench-cpu test-all clean docker-build up docker-mockgen logs down

# Variables
APP_NAME := httpmock
MAIN_PKG := ./cmd
BENCH ?= .
BENCH_COUNT ?= 1
BENCH_TIME ?= 1s

# docker compose: the "tools" profile holds the mockgenerator service, and
# BUILT_IMAGES are the images this compose file builds (pulled images such as
# prometheus and grafana are kept on down)
export TAG ?= dev
COMPOSE := docker compose --profile tools
BUILT_IMAGES := xmpl/mockrequest:$(TAG) xmpl/mockrequest-target:$(TAG) xmpl/mockrequest-mockgen:$(TAG)
PROMETHEUS_URL := http://localhost:19116
GRAFANA_URL := http://localhost:13016

help: ## Display this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-15s %s\n", $$1, $$2}'

generate: ## Regenerate gomock mocks (go generate)
	go generate ./...

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

docker-build: ## Build the Docker images
	$(COMPOSE) build

up: ## Start the prober, target, Prometheus and Grafana (detached, waits until healthy)
	docker compose up --build --detach --wait --wait-timeout 240
	@echo "Prometheus: $(PROMETHEUS_URL)"
	@echo "Grafana:    $(GRAFANA_URL)/d/mock-request (anonymous admin)"

docker-mockgen: ## Regenerate the mocks and run the tests in Docker (exits when done)
	$(COMPOSE) run --rm --build mockgenerator

logs: ## Follow the prober logs
	docker compose logs --follow httpmock

down: ## Remove this project's containers, networks, volumes, orphans and built images
	$(COMPOSE) down --volumes --remove-orphans
	-docker image rm $(BUILT_IMAGES)
