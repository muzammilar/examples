-- Every component of the cluster, as seen from SQL.
SELECT TIDB_VERSION()\G
SELECT type, instance, status_address, version FROM information_schema.cluster_info ORDER BY type, instance;
SELECT store_id, address, store_state_name, leader_count, region_count FROM information_schema.tikv_store_status ORDER BY store_id;
