# Runs the cluster in compose.yaml and the toolchain in Docker; nothing needs
# installing locally. `make help` lists the targets.

COMPOSE ?= docker compose
KV      := $(COMPOSE) run --rm --no-TTY client
# Limitation: this runs as root, so on Linux the files it creates in target/
# end up owned by root. Docker Desktop on macOS hides this.
RUST    := docker run --rm -v "$(CURDIR)":/src -v cargo-registry:/usr/local/cargo/registry -w /src rust:1

.DEFAULT_GOAL := help
.PHONY: help up stop down status logs kv load demo test bench lint quality check

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

up: ## Build and start the cluster, waiting until every node sees a leader
	$(COMPOSE) up --build --detach --wait kv1 kv2 kv3

stop: ## Stop the nodes, keeping their data for the next `make up`
	$(COMPOSE) stop

down: ## Remove the cluster: containers, network and data volumes
	$(COMPOSE) down --volumes --remove-orphans

status: ## Show each node's view of the cluster
	$(KV) status

logs: ## Follow the nodes' logs
	$(COMPOSE) logs --follow kv1 kv2 kv3

kv: ## Run a client command, e.g. make kv ARGS="put greeting hello"
	$(KV) $(ARGS)

load: ## Load-test the running cluster, e.g. make load ARGS="--requests 5000"
	$(KV) bench $(ARGS)

demo: up ## Walk through replication, failover, rebuilding a node, and a load test
	./demo.sh

test: ## Run the tests
	$(RUST) cargo test --locked

bench: ## Run the write-throughput benchmarks
	$(RUST) cargo bench --locked

lint: ## Check formatting and run clippy
	$(RUST) sh -c 'rustup component add clippy rustfmt && cargo fmt --check && cargo clippy --locked --all-targets -- -D warnings'

quality: ## Run cargo-deny, cargo-machete, hadolint and shellcheck
	@docker volume create cargo-registry >/dev/null
	$(COMPOSE) run --rm deny
	$(COMPOSE) run --rm machete
	$(COMPOSE) run --rm hadolint
	$(COMPOSE) run --rm shellcheck

check: lint test quality ## Run every check CI would
