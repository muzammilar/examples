-- lightweight transactions (Paxos): IF ... conditions return [applied] true/false plus the current values
CONSISTENCY QUORUM;
SERIAL CONSISTENCY SERIAL;
INSERT INTO demo.users (username, email, balance) VALUES ('bob', 'bob@example.com', 50) IF NOT EXISTS;
INSERT INTO demo.users (username, email, balance) VALUES ('bob', 'impostor@example.com', 0) IF NOT EXISTS;
-- compare-and-set: only the first update sees balance = 50
UPDATE demo.users SET balance = 80 WHERE username = 'bob' IF balance = 50;
UPDATE demo.users SET balance = 999 WHERE username = 'bob' IF balance = 50;
DELETE FROM demo.users WHERE username = 'alice' IF EXISTS;
-- a SERIAL read also sees any in-flight Paxos round (one partition at a time)
CONSISTENCY SERIAL;
SELECT * FROM demo.users WHERE username = 'bob';
