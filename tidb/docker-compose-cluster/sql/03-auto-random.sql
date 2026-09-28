USE demo;
SHOW CREATE TABLE customers\G
-- AUTO_RANDOM(5): the 5 bits below the sign bit are the shard (32 possible values),
-- the rest is an increasing counter. One shard per loading statement:
SELECT id >> 58 AS shard, COUNT(*) AS customers, MIN(id) AS first_id FROM customers GROUP BY shard ORDER BY shard;
INSERT INTO customers (name, country) VALUES ('newcomer', 'FR');
SELECT LAST_INSERT_ID() AS generated_id, LAST_INSERT_ID() >> 58 AS shard;
