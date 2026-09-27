
/* Members of cluster_hits as seen by this node. Nobody lists hosts in config: each data node */
/* registers itself in ClickHouse Keeper under /clickhouse/discovery/cluster_hits. */
SELECT cluster, shard_num, replica_num, host_name, is_local
FROM system.clusters
WHERE cluster = 'cluster_hits'
ORDER BY shard_num, replica_num;

/* The registrations themselves */
SELECT name, value FROM system.zookeeper WHERE path = '/clickhouse/discovery/cluster_hits/shards';
