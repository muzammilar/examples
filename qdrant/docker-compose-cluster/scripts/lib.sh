# Shared helpers for test.sh and failover.sh (POSIX sh, curl + jq).
N1=http://qdrant-1:6333
N2=http://qdrant-2:6333
N3=http://qdrant-3:6333
COLL=demo

req() { # METHOD URL FILE JQ_FILTER: print the body, send it, show the result
  echo "==> $1 ${2#http://}  < $3"; cat "$3"
  curl -sS --fail-with-body -X "$1" "$2" -H 'Content-Type: application/json' --data @"$3" | jq -c "$4"
  echo
}

# peers (id -> uri), this peer's Raft role and the leader's uri, as seen by NODE
cluster() { # NODE_URL
  curl -sS --fail-with-body "$1/cluster" | jq -c '.result as $r | {
    peer: $r.peers[$r.peer_id | tostring].uri,
    peers: [$r.peers | to_entries[] | .value.uri] | sort,
    role: $r.raft_info.role,
    leader: $r.peers[$r.raft_info.leader | tostring].uri,
    term: $r.raft_info.term,
    send_failures: ($r.message_send_failures | map_values(.count))}'
}

# shard -> [peer: state] for collection COLL, as seen by NODE (local + remote replicas)
placement() { # NODE_URL
  peers=$(curl -sS --fail-with-body "$1/cluster" | jq -c '.result.peers | map_values(.uri | capture("//(?<h>[^:/]+)").h)')
  curl -sS --fail-with-body "$1/collections/$COLL/cluster" | jq -c --argjson p "$peers" '.result as $r |
    ([$r.local_shards[] | {shard_id, peer_id: $r.peer_id, state}] + [$r.remote_shards[] | {shard_id, peer_id, state}])
    | group_by(.shard_id)[] | {shard: .[0].shard_id, replicas: map("\($p[.peer_id | tostring]): \(.state)")}'
}

# 0 if every replica of every shard is Active and no shard transfer is running
all_active() { # NODE_URL
  curl -sS "$1/collections/$COLL/cluster" | jq -e '.result as $r | ($r.shard_transfers | length) == 0 and
    ([$r.local_shards[].state, $r.remote_shards[].state] | length == 6 and all(. == "Active"))' >/dev/null 2>&1
}

count() { # NODE_URL: exact point count across all shards
  curl -sS --fail-with-body -X POST "$1/collections/$COLL/points/count" -H 'Content-Type: application/json' \
    -d '{"exact": true}' | jq '.result.count'
}

expect() { # ACTUAL EXPECTED WHAT
  if [ "$1" = "$2" ]; then echo "ok: $3 = $1"; else echo "FAIL: $3 = $1, expected $2"; exit 1; fi
}
