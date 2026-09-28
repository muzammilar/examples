-- ALL = 3 of 3 replicas: Unavailable while a node is down
CONSISTENCY ALL;
SELECT * FROM demo.kv WHERE k = 'failover';
