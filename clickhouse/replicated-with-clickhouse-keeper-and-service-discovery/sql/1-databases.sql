/* Run on EVERY data node, including nodes added later (scripts/add-node.sh does it for them). */
/* A Replicated database keeps its table definitions in Keeper: once a node creates the database, */
/* it replays every CREATE/ALTER/DROP ever run in it, so new nodes get the full schema automatically. */
/* Cluster discovery only updates cluster membership, not schema, so the two complement each other. */
/* {shard} and {replica} come from macros.xml (SHARD env var and the hostname). */

CREATE DATABASE IF NOT EXISTS test ENGINE = Replicated('/clickhouse/databases/test', '{shard}', '{replica}');

/* A separate database for Materialized Views in case you want to recreate them */
CREATE DATABASE IF NOT EXISTS test_mvs ENGINE = Replicated('/clickhouse/databases/test_mvs', '{shard}', '{replica}');
