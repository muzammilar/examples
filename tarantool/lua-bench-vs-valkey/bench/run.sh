#!/bin/sh
# Runs benchmark setups one after another, each on fresh containers and volumes:
# start the setup, wait until it is ready, print its durability settings, run the client
# (go/lua-bench/tarantool or go/rueidis-lua-bench), remove the setup.
#   sh bench/run.sh single-tarantool-write single-valkey-everysec ...
#   sh bench/run.sh list        # every setup name
# Output: results/<setup>.txt. BENCH_N, BENCH_C, BENCH_KEYS override -n, -c, -keys.
set -eu; set -o pipefail  # tee below
cd "$(dirname "$0")/.."

FLAGS="-n ${BENCH_N:-1000000} -c ${BENCH_C:-50} -keys ${BENCH_KEYS:-100000}"
ALL="single-tarantool-none single-tarantool-write single-tarantool-fsync
single-valkey-none single-valkey-no single-valkey-everysec single-valkey-always
rs-tarantool-write rs-tarantool-fsync rs-tarantool-sync-write
rs-tarantool-tmpfs-write rs-tarantool-tmpfs-sync-write
rs-valkey-none rs-valkey-none-wait1 rs-valkey-everysec rs-valkey-always rs-valkey-everysec-wait1
shard-tarantool-write shard-valkey-everysec"

# lua on a Tarantool instance as app/secret; prints tt's YAML reply
lua() { echo "$2" | docker compose exec -T "$1" tt connect "app:secret@$1:3301" -f-; }
until_true() { # SECONDS SERVICE LUA
	i=0
	until lua "$2" "$3" 2>/dev/null | grep -qx -- '- true'; do
		i=$((i + 1)); [ $i -lt "$1" ] || { echo "timed out: $3 on $2" >&2; exit 1; }; sleep 1
	done
}
valkey_settings() {
	echo "$1: $(docker compose exec -T "$1" valkey-cli config get appendonly appendfsync save | tr '\n' ' ')"
}

# SETUP is <layout>-<system>-<option>...: layout single | rs | shard; options
#   Tarantool: none | write | fsync (wal.mode), sync (synchronous spaces), tmpfs (WAL on tmpfs)
#   Valkey:    none (no AOF) | no | everysec | always (appendfsync), wait1 (WAIT 1 0 per write)
one() {
	name=$1
	profile=$(echo "$name" | cut -d- -f1-2)
	WAL_MODE=write VALKEY_PERSIST="--appendonly no" BENCH_SYNC=0 COMPOSE_FILE=docker-compose.yml extra=""
	for opt in $(echo "$name" | cut -d- -f3- | tr - ' '); do
		case $profile-$opt in
		*-tarantool-none | *-tarantool-write | *-tarantool-fsync) WAL_MODE=$opt ;;
		rs-tarantool-sync) BENCH_SYNC=1 ;;
		rs-tarantool-tmpfs) COMPOSE_FILE=docker-compose.yml:docker-compose.tmpfs.yml ;;
		*-valkey-none) VALKEY_PERSIST="--appendonly no" ;;
		*-valkey-no | *-valkey-everysec | *-valkey-always) VALKEY_PERSIST="--appendonly yes --appendfsync $opt" ;;
		rs-valkey-wait1) extra="-wait 1" ;;
		*) echo "unknown setup $name (sh bench/run.sh list)" >&2; exit 2 ;;
		esac
	done
	export COMPOSE_FILE
	export WAL_MODE VALKEY_PERSIST BENCH_SYNC

	echo "==> $name: $profile, WAL_MODE=$WAL_MODE BENCH_SYNC=$BENCH_SYNC VALKEY_PERSIST='$VALKEY_PERSIST' COMPOSE_FILE=$COMPOSE_FILE $extra"
	docker compose --profile "$profile" up --detach --wait --quiet-pull
	case $profile in
	single-tarantool) client=lua-bench-tarantool addr="-addr tarantool:3301 -user app -password secret"
		lua tarantool 'return box.cfg.wal_mode' | grep -- '^- ' ;;
	rs-tarantool) client=lua-bench-tarantool addr="-addr tarantool-1:3301 -user app -password secret"
		until_true 60 tarantool-1 'return box.info.ro == false and box.info.election.state == "leader"'
		[ "$BENCH_SYNC" = 0 ] || until_true 30 tarantool-1 'return box.space.bench_s ~= nil and box.space.bench_s.is_sync'
		for i in 1 2 3; do lua tarantool-$i 'return {box.info.name, box.cfg.wal_mode, box.cfg.wal_dir, box.info.election.state, box.space.bench_s and box.space.bench_s.is_sync or false}' |
			tr -d '\n' | sed 's/^---//; s/\.\.\.$//'; echo; done
		docker compose exec -T tarantool-1 df -h /var/lib/tarantool | tail -n 1 ;;
	shard-tarantool) client=lua-bench-tarantool addr="-addr tarantool-router:3301 -user app -password secret -router"
		until_true 120 tarantool-router 'return bench_ready()'
		for s in a b c; do lua tarantool-storage-$s 'return {box.info.name, box.cfg.wal_mode}' | tr -d '\n' | sed 's/^---//; s/\.\.\.$//'; echo; done ;;
	single-valkey) client=lua-bench-valkey addr="-addr valkey:6379"
		valkey_settings valkey ;;
	rs-valkey) client=lua-bench-valkey addr="-addr valkey-primary:6379"
		for s in valkey-primary valkey-replica-1 valkey-replica-2; do valkey_settings $s; done
		docker compose exec -T valkey-primary valkey-cli info replication | grep -E '^(role|connected_slaves):' ;;
	shard-valkey) client=lua-bench-valkey addr="-addr valkey-shard-1:6379"
		docker compose --profile shard-valkey-init run --rm --quiet-pull valkey-shard-init
		for s in 1 2 3; do valkey_settings valkey-shard-$s; done ;;
	esac
	# shellcheck disable=SC2086
	docker compose --profile tools run --rm -T $client $addr $FLAGS $extra
	docker compose --profile "$profile" --profile shard-valkey-init down --volumes --remove-orphans
}

case "${1:-}" in
list) echo "$ALL"; exit 0 ;;
"") echo "usage: $0 SETUP... | list" >&2; exit 2 ;;
esac
mkdir -p results
docker compose --profile tools build --quiet
for name in "$@"; do
	{ date -u +%Y-%m-%dT%H:%M:%SZ; one "$name"; } 2>&1 | tee "results/$name.txt"
done
