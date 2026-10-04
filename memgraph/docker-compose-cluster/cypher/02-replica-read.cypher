// Runs on each replica: same counts as MAIN, and the index was replicated too.
SHOW REPLICATION ROLE;
MATCH (a:Account) WITH count(a) AS accounts MATCH ()-[p:PAID]->() RETURN accounts, count(p) AS payments, sum(p.amount) AS total;
SHOW INDEX INFO;
MATCH (a:Account)<-[p:PAID]-() RETURN a.id AS account, sum(p.amount) AS received ORDER BY received DESC, account LIMIT 3;
