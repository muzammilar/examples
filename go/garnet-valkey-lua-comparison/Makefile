.PHONY: help build run test test-containers clean docker-build docker-up docker-down check-results deps fmt vet

# Variables
APP_NAME := app
DOCKER_IMAGE := garnet-valkey-lua-comparison
DOCKER_TAG := latest

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
	go build -o bin/$(APP_NAME) ./cmd/app

run: ## Run the application
	go run ./cmd/app

test: ## Run tests
	go test -v ./...

test-containers: deps ## Run tests with testcontainers (ensures deps are installed)
	go test -v -timeout 5m ./...

clean: ## Clean build artifacts
	rm -rf bin/
	go clean

docker-build: ## Build Docker image
	docker build -t $(DOCKER_IMAGE):$(DOCKER_TAG) .

docker-up: ## Start services with docker-compose
	docker-compose up -d

docker-down: ## Stop services and remove volumes
	docker-compose down -v

check-results: ## Show application logs and performance results
	docker logs garnet-valkey-app

docker-logs: ## Show docker-compose logs
	docker-compose logs -f

docker-run: docker-build docker-up ## Build and run with docker-compose

all: deps fmt vet build ## Run all build steps
