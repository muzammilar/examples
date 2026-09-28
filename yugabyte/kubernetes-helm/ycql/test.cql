-- YCQL keyspaces take their replication from the universe (RF of the cluster).
CREATE KEYSPACE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS accounts;

-- JSONB column plus per-row TTL
CREATE TABLE users (id int PRIMARY KEY, name text, profile jsonb);
INSERT INTO users (id, name, profile) VALUES (1, 'alice', '{"city": "Oslo", "tags": ["admin"]}');
INSERT INTO users (id, name, profile) VALUES (2, 'bob', '{"city": "Lima"}') USING TTL 60;
SELECT id, name, profile->>'city' AS city, TTL(name) FROM users;
SELECT name FROM users WHERE profile->>'city' = 'Oslo' ALLOW FILTERING;

-- Secondary indexes require a transactional table
CREATE TABLE accounts (id int PRIMARY KEY, owner text, balance int)
  WITH transactions = {'enabled': true};
CREATE INDEX accounts_by_owner ON accounts (owner);
INSERT INTO accounts (id, owner, balance) VALUES (1, 'alice', 100);
INSERT INTO accounts (id, owner, balance) VALUES (2, 'bob', 100);

BEGIN TRANSACTION
  UPDATE accounts SET balance = 70 WHERE id = 1;
  UPDATE accounts SET balance = 130 WHERE id = 2;
END TRANSACTION;

SELECT * FROM accounts WHERE owner = 'bob';
SELECT id, owner, balance, partition_hash(id) AS hash FROM accounts;
