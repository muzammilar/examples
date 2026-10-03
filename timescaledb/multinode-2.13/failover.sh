#!/bin/bash
# `make failover`: stop data node dn2 and walk through what multi-node 2.13 does about it.
# Nothing here is automatic: the access node does not notice a dead data node by itself, and
# re-replication is a manual, experimental procedure.
set -uo pipefail
PSQL=(docker compose --progress quiet run --rm --no-TTY psql -X -v ON_ERROR_STOP=0)
q() { echo; echo "-- $1"; shift; "${PSQL[@]}" "$@" 2>&1 | grep -v -E 'is deprecated|^DETAIL:  Multi-node'; }

q "before: rows and the newest chunks" -c "SELECT count(*) FROM conditions" \
	-c "SELECT chunk_name, data_nodes FROM timescaledb_information.chunks WHERE hypertable_name = 'conditions' ORDER BY range_start DESC, chunk_name LIMIT 3"

echo; echo "==> docker stop tsdb-dn2"
docker stop tsdb-dn2 >/dev/null

q "every query touching the hypertable fails, although each chunk has a replica elsewhere" \
	-c "SELECT count(*) FROM conditions"

q "mark dn2 unavailable: reads go to the remaining replicas" \
	-c "SELECT node_name, available FROM alter_data_node('dn2', available => false)" \
	-c "SELECT count(*) FROM conditions"

q "writes still work: into existing chunks (dn2 is dropped from their replica list) and new ones (1 replica only)" \
	-c "INSERT INTO conditions SELECT now() - interval '1 minute' * g, g % 300 + 1, 99 FROM generate_series(1, 1000) g" \
	-c "INSERT INTO conditions SELECT now() + interval '1 day' + interval '1 minute' * g, g % 300 + 1, 99 FROM generate_series(1, 1000) g" \
	-c "SELECT count(*) FROM conditions"

echo; echo "==> docker start tsdb-dn2"
docker start tsdb-dn2 >/dev/null
until docker exec tsdb-dn2 pg_isready -q -U postgres; do sleep 1; done

q "dn2 is back but holds stale copies; these chunks are under-replicated" \
	-c "SELECT node_name, available FROM alter_data_node('dn2', available => true)" \
	-c "SELECT chunk_name, num_replicas, replica_nodes, non_replica_nodes FROM timescaledb_experimental.chunk_replication_status WHERE num_replicas < desired_num_replicas ORDER BY 1"

# repair: drop dn2's stale copy if there is one, then copy the chunk from a live replica
# (timescaledb_experimental.copy_chunk, logical replication between data nodes)
q "re-replicate with timescaledb_experimental.copy_chunk" <<'EOF'
\timing on
SELECT format('CALL distributed_exec(%L, node_list => %L)',
              format('DROP TABLE IF EXISTS %I.%I', chunk_schema, chunk_name), ARRAY[dst]),
       format('CALL timescaledb_experimental.copy_chunk(%L, %L, %L)',
              format('%I.%I', chunk_schema, chunk_name), replica_nodes[1], dst)
FROM (SELECT *, CASE WHEN 'dn2' = ANY (non_replica_nodes) THEN 'dn2' ELSE non_replica_nodes[1] END AS dst
      FROM timescaledb_experimental.chunk_replication_status
      WHERE num_replicas < desired_num_replicas) s
ORDER BY chunk_name \gexec
EOF

q "after the repair" \
	-c "SELECT count(*) FILTER (WHERE num_replicas < desired_num_replicas) AS under_replicated, count(*) AS chunks FROM timescaledb_experimental.chunk_replication_status" \
	-c "SELECT count(*) FROM conditions" \
	-c "DELETE FROM conditions WHERE temperature = 99"
