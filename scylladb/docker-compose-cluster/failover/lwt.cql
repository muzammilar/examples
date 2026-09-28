-- Lightweight transactions (Paxos) need a quorum of replicas, not all of them
CONSISTENCY QUORUM;
DELETE FROM demo.kv WHERE k = 'lwt' IF EXISTS;
INSERT INTO demo.kv (k, v) VALUES ('lwt', 'v1') IF NOT EXISTS;
UPDATE demo.kv SET v = 'v2' WHERE k = 'lwt' IF v = 'v1';
UPDATE demo.kv SET v = 'v3' WHERE k = 'lwt' IF v = 'v1';
SELECT * FROM demo.kv WHERE k = 'lwt';
