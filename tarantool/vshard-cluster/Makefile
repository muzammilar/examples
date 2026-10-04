.PHONY: up test scale-out scale-in status cli logs down

SHELL := bash
.SHELLFLAGS := -o pipefail -c
, := ,

CONFIG = { sh scripts/config.sh $(1) > app/config.yaml.tmp && mv app/config.yaml.tmp app/config.yaml; }
# Lua on container $(1) through tt connect, YAML output
LUA = echo '$(2)' | docker exec -i tarantool-$(1) tt connect app:secret@$(1):3301 -f-
RUNNING = $$(docker ps --filter label=com.docker.compose.project=tarantool-vshard --format '{{.Names}}' | \
	grep -E 'storage|router' | sed 's/^tarantool-//' | sort)
# config:reload() on every running instance (each re-reads app/config.yaml)
RELOAD = for i in $(RUNNING); do $(call LUA,$$i,require("config"):reload()) >/dev/null || exit 1; done
BUCKETS = $(call LUA,router-a-001,return buckets_text()) | sed -n 's/^[ -]*- //p'
NOW = perl -MTime::HiRes=time -e 'printf "%.1f", time - ($$ARGV[0] // 0)'
CLIENT = docker compose --progress quiet run --rm -T client

up:
	@[ -f app/config.yaml ] || $(call CONFIG,a b)
	docker compose up --detach --wait --build
	@for t in $$(seq 1 60); do $(call LUA,router-a-001,return settled("storage-a"$(,) 1500)) 2>/dev/null | grep -q '^- true' && break; sleep 1; done
	$(MAKE) --no-print-directory status

test:
	$(CLIENT) scripts/smoke.lua

# wait until storage-$(1) has $(2) active buckets and nothing is in flight; print the time
define WAIT_BUCKETS
t0=$$($(NOW)); until $(call LUA,router-a-001,return settled("storage-$(1)"$(,) $(2))) 2>/dev/null | grep -q '^- true'; do sleep 1; done; \
echo "==> $$(date -u +%T) storage-$(1) has $(2) buckets after $$($(NOW) $$t0) s"
endef

# 2 -> 3 replicasets under load: start storage-c, add it to the config, reload every instance;
# the rebalancer moves 1,000 buckets to it (1500/1500 -> 1000/1000/1000)
export LOAD_DURATION ?= 60
export WRITERS ?= 16
scale-out:
	@RUN=$$RANDOM $(CLIENT) scripts/load.lua & load=$$!; sleep 10; \
	$(call CONFIG,a b c); echo "==> $$(date -u +%T) config with storage-c written, starting storage-c-001, storage-c-002"; \
	docker compose --profile scale up --detach --wait storage-c-001 storage-c-002 2>/dev/null; \
	$(RELOAD); echo "==> $$(date -u +%T) config reloaded on every instance"; \
	$(call WAIT_BUCKETS,c,1000); \
	wait $$load; rc=$$?; $(MAKE) --no-print-directory status; exit $$rc

# 3 -> 2 under load: weight 0 for storage-c drains it, then drop it from the config and stop it
scale-in:
	@RUN=$$RANDOM $(CLIENT) scripts/load.lua & load=$$!; sleep 10; \
	$(call CONFIG,a b c=0); $(RELOAD); echo "==> $$(date -u +%T) storage-c weight 0"; \
	$(call WAIT_BUCKETS,c,0); \
	docker compose --profile scale rm --stop --force --volumes storage-c-001 storage-c-002 2>/dev/null; \
	$(call CONFIG,a b); $(RELOAD); \
	echo "==> $$(date -u +%T) storage-c stopped and removed from the config"; \
	wait $$load; rc=$$?; $(MAKE) --no-print-directory status; exit $$rc

status:
	docker compose --profile scale ps --format 'table {{.Name}}\t{{.Status}}'
	@$(BUCKETS) 2>/dev/null

cli:
	docker exec -it tarantool-router-a-001 tt connect app:secret@router-a-001:3301

logs:
	docker compose --profile scale logs --follow

down:
	docker compose --profile scale --profile tools down --volumes --remove-orphans --rmi local
	rm -f app/config.yaml
