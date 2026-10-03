#!/bin/bash
# `make test`: runs in the `client` service (meta image: curl + influxd-ctl). Recreates database
# `fleet` (replication factor 2) each run.
set -euo pipefail
D1=http://data-1:8086 D2=http://data-2:8086
CTL="influxd-ctl -bind meta-1:8091"

step() { printf '\n==== %s\n' "$*"; }
q() { # NODE QUERY: InfluxQL through /query, CSV output
	curl -sS -G -H 'Accept: application/csv' "$1/query" --data-urlencode db=fleet --data-urlencode "q=$2"
	echo
}
write() { # NODE CONSISTENCY LINES
	curl -sS -w "write via ${1#http://} consistency=$2 -> HTTP %{http_code}\n" \
		"$1/write?db=fleet&precision=s&consistency=$2" --data-binary "$3"
}

step "1. cluster members (3 meta nodes in Raft, 2 data nodes)"
$CTL show

step "2. database fleet with REPLICATION 2: every shard lives on both data nodes"
q "$D1" "DROP DATABASE fleet" >/dev/null
q "$D1" "CREATE DATABASE fleet WITH DURATION 30d REPLICATION 2 SHARD DURATION 1d NAME month"
# JSON here: the CSV writer prints the integer replicaN column as the text "replicaN"
curl -sS -G "$D2/query" --data-urlencode "q=SHOW RETENTION POLICIES ON fleet"
echo

step "3. writes: through data-1 with consistency=all, through data-2 with consistency=quorum"
now=$(date +%s)
lines=""
for h in $(seq 1 20); do
	for m in 3 2 1; do lines+="cpu,host=host-$h,region=r$((h % 4)) usage=$((h * 3 + m)).5,load=$((h % 8))i $((now - m * 60))"$'\n'; done
done
write "$D1" all "$(echo "$lines" | head -30)"
write "$D2" quorum "$(echo "$lines" | tail -n +31)"

step "4. reads: each data node answers for the whole cluster (InfluxQL)"
q "$D1" "SELECT count(usage) FROM cpu"
q "$D2" "SELECT mean(usage), max(load) FROM cpu WHERE time > now() - 10m GROUP BY region"
q "$D2" "SELECT last(usage) FROM cpu WHERE host = 'host-7'"
q "$D1" "SHOW TAG VALUES CARDINALITY FROM cpu WITH KEY = host"

step "5. shards: owners are both data nodes (ids from step 1)"
$CTL show-shards | grep -E 'ID|fleet|^-'
