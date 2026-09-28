# Three servers in cluster "demo", all with JetStream, agreeing on one meta leader.
# `nats server ...` talks to the system account (user sys).
SYS="--user sys --password sys"

nats $SYS server list 3
names=$(nats $SYS server list 3 --json | jq -r '[.[] | select(.server.cluster == "demo" and .server.jetstream)] | map(.server.name) | sort | join(",")')
echo "servers with JetStream: $names"
[ "$names" = "nats-1,nats-2,nats-3" ] || { echo "FAIL: expected nats-1,nats-2,nats-3"; exit 1; }

nats $SYS server report jetstream
leaders=$(nats $SYS server request jetstream 3 | jq -r '.data.meta_cluster.leader' | sort -u)
echo "meta leader seen by every server: $leaders"
[ "$(echo "$leaders" | wc -l)" -eq 1 ] && [ -n "$leaders" ] && [ "$leaders" != null ] ||
	{ echo "FAIL: servers disagree on the meta leader"; exit 1; }
