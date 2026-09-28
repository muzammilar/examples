USE demo;
-- Split orders into 8 regions over the positive BIGINT range (where AUTO_RANDOM ids
-- land) and scatter them: PD spreads the new region leaders over the three stores.
-- (Usually done right after CREATE TABLE, before a bulk load.)
SET SESSION tidb_scatter_region = 'table';
SPLIT TABLE orders BETWEEN (0) AND (9223372036854775807) REGIONS 8;
SHOW TABLE orders REGIONS;

-- Every region has 3 replicas (one per store); leaders are spread over the stores.
SELECT p.store_id, COUNT(*) AS peers, SUM(p.is_leader) AS leaders
FROM information_schema.tikv_region_status s
JOIN information_schema.tikv_region_peers p ON p.region_id = s.region_id
WHERE s.db_name = 'demo' AND s.table_name = 'orders' AND s.is_index = 0
GROUP BY p.store_id ORDER BY p.store_id;

-- Rows per split region: region i holds ids in [i * 2^60, (i + 1) * 2^60).
SELECT id >> 60 AS split_region, COUNT(*) AS orders_rows FROM orders GROUP BY split_region ORDER BY split_region;
