# Runs the cluster in compose.yaml, and the toolchain in Docker. Nothing
# needs installing locally. `make help` lists the targets.

COMPOSE ?= docker compose
SEND    := $(COMPOSE) run --rm --no-TTY --no-deps client
# Limitation: this runs as root. On Linux the files it creates in _build/
# end up owned by root; Docker Desktop on macOS hides this.
REBAR   := docker run --rm -i -v "$(CURDIR)":/src -v rebar3-cache:/root/.cache/rebar3 -w /src erlang:27 rebar3

# Cluster size: 3, 4 or 5 nodes. node4 and node5 are compose profiles.
NODES ?= 3
NODE_PROFILES_3 :=
NODE_PROFILES_4 := node4
NODE_PROFILES_5 := node4,node5
export COMPOSE_PROFILES := $(NODE_PROFILES_$(NODES))

.DEFAULT_GOAL := help
.PHONY: help up down logs send nodes status bench demo test lint quality check shell

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-8s %s\n", $$1, $$2}'

# The Erlang cookie lets nodes join each other, and anyone who has it can
# run code on them. It's generated locally and never committed.
.env:
	@printf 'ERLANG_COOKIE=%s\n' "$$(od -An -tx1 -N24 /dev/urandom | tr -d ' \n')" > $@

up: .env ## Start the cluster (NODES=3, 4 or 5) and wait until it's healthy
	@case "$(NODES)" in 3|4|5) ;; *) echo "NODES must be 3, 4 or 5" >&2; exit 2;; esac
	$(COMPOSE) up --build --detach --wait

down: .env ## Remove the cluster: containers, network and volumes
	COMPOSE_PROFILES=node4,node5 $(COMPOSE) down --volumes --remove-orphans

logs: .env ## Follow every node's logs
	$(COMPOSE) logs --follow

send: .env ## Send requests through the load balancer, e.g. make send REQ='fib 30 key=k\nstatus'
	@printf '%b\n' "$(REQ)" | $(SEND)

nodes: .env ## List the cluster's members
	@printf 'nodes\n' | $(SEND)

status: .env ## Show queue depth and worker use on whichever node answers
	@printf 'status\n' | $(SEND)

bench: .env ## Load-test through the load balancer, e.g. make bench ARGS="--task 'sleep 10'"
	$(COMPOSE) run --rm --no-deps bench $(ARGS)

demo: up ## Walk through key ownership, load, and losing a node
	./demo.sh

test: ## Run eunit, and Common Test including a multi-node cluster suite
	$(REBAR) do eunit, ct

lint: ## Check formatting, then run xref and dialyzer
	$(REBAR) do fmt --check, xref, dialyzer

quality: .env ## Run elvis, hank, hadolint and shellcheck
	$(REBAR) do lint, hank
	$(COMPOSE) run --rm hadolint
	$(COMPOSE) run --rm shellcheck

check: lint test quality ## Run lint, test and quality

shell: ## Start an Erlang shell with the app running
	docker run --rm -it -v "$(CURDIR)":/src -v rebar3-cache:/root/.cache/rebar3 -w /src erlang:27 rebar3 shell
