.PHONY: up test status cli cli-valkey benchmark bench-single bench-replicated bench-sharded sync-wal-none table down

SHELL := bash
.SHELLFLAGS := -o pipefail -c

CLIENTS := tarantool-lua-bench-client:local tarantool-lua-bench-valkey-client:local tarantool-lua-bench-vshard:local
SMOKE := -n 20000 -c 50 -keys 10000

# one Tarantool (wal.mode write) and one Valkey (no AOF) for `make test` / `make cli`; the
# benchmark targets start their own setups
up:
	docker compose --profile tools build --quiet
	docker compose --profile single-tarantool --profile single-valkey up --detach --wait

# both clients with a small run; each checks its CAS results and exits non-zero on an error
test:
	docker compose --profile tools run --rm -T lua-bench-tarantool -addr tarantool:3301 -user app -password secret $(SMOKE)
	docker compose --profile tools run --rm -T lua-bench-valkey -addr valkey:6379 $(SMOKE)

status:
	docker compose --profile '*' ps --format 'table {{.Name}}\t{{.Status}}'

cli:
	docker compose exec tarantool tt connect app:secret@tarantool:3301

cli-valkey:
	docker compose exec valkey valkey-cli

# the full matrix, one setup at a time on fresh containers (stops `make up` first)
benchmark: bench-single bench-replicated bench-sharded

bench-single:
	docker compose --profile '*' down --volumes --remove-orphans
	sh bench/run.sh single-tarantool-none single-tarantool-write single-tarantool-fsync \
		single-valkey-none single-valkey-no single-valkey-everysec single-valkey-always

bench-replicated:
	docker compose --profile '*' down --volumes --remove-orphans
	sh bench/run.sh rs-tarantool-write rs-tarantool-fsync rs-tarantool-sync-write \
		rs-tarantool-tmpfs-write rs-tarantool-tmpfs-sync-write \
		rs-valkey-none rs-valkey-none-wait1 rs-valkey-everysec rs-valkey-always rs-valkey-everysec-wait1

bench-sharded:
	docker compose --profile '*' down --volumes --remove-orphans
	sh bench/run.sh shard-tarantool-write shard-valkey-everysec

# synchronous spaces with wal.mode none: the replicas never bootstrap ("Replication does not
# support wal_mode = 'none'"), the leader runs alone with synchro quorum 1
sync-wal-none:
	docker compose --profile '*' down --volumes --remove-orphans
	WAL_MODE=none BENCH_SYNC=1 docker compose --profile rs-tarantool up --detach --wait
	sleep 15
	-docker compose logs tarantool-2 | grep -m 1 'Replication does not support'
	echo 'return {box.info.election.state, box.info.synchro.quorum, #box.info.replication}' | \
		docker compose exec -T tarantool-1 tt connect app:secret@tarantool-1:3301 -f-
	-echo 'return box.info.status' | docker compose exec -T tarantool-2 tt connect app:secret@tarantool-2:3301 -f-
	docker compose --profile rs-tarantool down --volumes --remove-orphans

# Markdown rows for two result files: make table T=single-tarantool-write V=single-valkey-everysec
table:
	sh bench/table.sh results/$(T).txt results/$(V).txt

down:
	docker compose --profile '*' down --volumes --remove-orphans --rmi local
	docker image rm $(CLIENTS) 2>/dev/null || true
