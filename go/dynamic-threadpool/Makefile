.PHONY: help deps fmt vet build run test test-race bench bench-cpu test-all clean docker-build docker-up docker-down

# Variables
APP_NAME := workerpool
MAIN_PKG := ./cmd/workerpool
BENCH ?= .
BENCH_COUNT ?= 1
BENCH_TIME ?= 1s

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

docker-up: ## Start services
	docker compose up --build

docker-down: ## Stop services and remove images and volumes
	docker compose down --rmi all --volumes
