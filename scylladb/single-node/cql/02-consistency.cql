-- the consistency level is chosen per request; with RF=1, ONE, QUORUM and ALL all mean the one replica
CONSISTENCY QUORUM;
INSERT INTO demo.users (username, email, balance) VALUES ('alice', 'alice@example.com', 100);
CONSISTENCY ALL;
SELECT * FROM demo.users WHERE username = 'alice';
-- read through the secondary index at ONE
CONSISTENCY ONE;
SELECT * FROM demo.users WHERE email = 'alice@example.com';
