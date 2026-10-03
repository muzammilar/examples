#!/bin/sh
# Delete the primary of shard 0 and wait for one of its replicas to take over.
set -e
k="kubectl --context kind-valkey-operator -n valkey"
role() { $k exec "$1" -c server -- env -u VALKEYCLI_AUTH valkey-cli role 2>/dev/null | head -1; }

replicas=
for p in $($k get pods -l valkey.io/shard-index=0 -o jsonpath='{.items[*].metadata.name}'); do
	if [ "$(role "$p")" = master ]; then primary=$p; else replicas="$replicas $p"; fi
done
set -- $replicas
./keys.sh write 1000 "$1"

echo "deleting primary $primary"
start=$(date +%s)
$k delete pod "$primary" --wait=false
for i in $(seq 60); do
	for r in $replicas; do [ "$(role "$r")" = master ] && new=$r; done
	[ -n "$new" ] && break
	sleep 1
done
[ -n "$new" ] || { echo "no replica took over"; exit 1; }
echo "$new is primary after $(($(date +%s) - start))s"
./keys.sh check 1000 "$new"
./wait-healthy.sh
