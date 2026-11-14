#!/bin/sh
set -eu
for pair in '0:100'; do
 shard=${pair%:*};uid=${pair#*:}
 current=$(sh scripts/primary.sh "$shard" || true)
 if [ -z "$current" ]; then
  alias=$(printf 'zone1-%010d' "$uid")
  sh scripts/ctl.sh PlannedReparentShard --new-primary "$alias" --wait-replicas-timeout=90s "orders/$shard"
 fi
done
sh scripts/ctl.sh ApplySchema --sql-file=/sql/schema.sql orders
sh scripts/ctl.sh ApplyVSchema --vschema-file=/config/vschema.json orders
sh scripts/ctl.sh ValidateSchemaKeyspace orders

# Schema propagation and VTGate serving discovery can lag the control-plane RPC.
for shard in 0; do
 ready=false
 for attempt in $(seq 90); do
  if docker compose exec -T vtgate mysql -h127.0.0.1 -P15306 -uroot -D"orders:$shard@primary" -Nse "SELECT count(*) FROM customers" > /dev/null 2>&1; then ready=true; break; fi
  sleep 1
 done
 [ "$ready" = true ] || { echo "VTGate primary for $shard never became queryable" >&2; exit 1; }
done
