#!/bin/sh
set -eu
shard=${1:-0}
sh scripts/ctl.sh GetShard "orders/$shard" | python3 -c '
import json,sys
s=json.load(sys.stdin)["shard"]
a=s.get("primary_alias")
if not a or not a.get("uid"):sys.exit(1)
print("t"+str(a["uid"]))
'
