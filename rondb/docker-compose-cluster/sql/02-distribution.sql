-- Rows are hash-partitioned on the primary key into fragments. With one node group
-- of 2 data nodes (NoOfReplicas=2) each fragment has a primary replica on one node
-- and a backup replica on the other.
USE demo;

SELECT n.node_id, m.group_id AS node_group, n.status, m.president, m.arbitrator
FROM ndbinfo.nodes n JOIN ndbinfo.membership m USING (node_id);

-- rows per partition (= fragment); RonDB creates 2 partitions per data node by default
SELECT partition_name, table_rows
FROM information_schema.partitions WHERE table_schema = 'demo' AND table_name = 'accounts';

-- rows held per fragment per data node: every fragment is stored on both nodes
SELECT node_id, fragment_num, fixed_elem_count AS row_count
FROM ndbinfo.memory_per_fragment
WHERE fq_name = 'demo/def/accounts'
ORDER BY fragment_num, node_id;

-- the node that currently owns the primary replica of each fragment
SELECT DISTINCT tf.partition_id AS fragment, tf.current_primary AS primary_node,
       tf.current_first_backup AS backup_node, tf.num_alive_replicas AS alive_replicas
FROM ndbinfo.table_fragments tf
JOIN ndbinfo.dict_obj_info o ON o.id = tf.table_id AND o.type = 2
WHERE o.fq_name = 'demo/def/accounts'
ORDER BY fragment;
