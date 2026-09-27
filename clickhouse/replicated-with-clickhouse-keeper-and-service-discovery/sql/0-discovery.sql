
/* Clusters and members as seen by this node. Nobody lists hosts in config: each data node */
/* registers itself in ClickHouse Keeper under /clickhouse/discovery/<cluster>. */
SELECT cluster, shard_num, replica_num, host_name, is_local
FROM system.clusters
WHERE cluster LIKE 'cluster_%'
ORDER BY cluster, shard_num, replica_num;

/* The registrations for cluster_hits themselves */
SELECT name, value FROM system.zookeeper WHERE path = '/clickhouse/discovery/cluster_hits/shards';
