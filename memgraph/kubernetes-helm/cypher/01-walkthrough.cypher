// Runs in the pod via mgconsole. Clean start, so `make test` can be re-run.
MATCH (n) WHERE NOT n:Tick DETACH DELETE n;
CREATE INDEX ON :Account(id);
UNWIND range(1, 1000) AS i CREATE (:Account {id: i});
UNWIND range(1, 5000) AS i
MATCH (a:Account {id: (i * 7) % 1000 + 1}), (b:Account {id: (i * 13) % 1000 + 1})
CREATE (a)-[:PAID {amount: i % 50 + 1}]->(b);
MATCH (a:Account) WITH count(a) AS accounts MATCH ()-[p:PAID]->() RETURN accounts, count(p) AS payments, sum(p.amount) AS total;
MATCH (:Account {id: 8})-[:PAID *1..2]->(b) RETURN count(DISTINCT b) AS within_2_hops;
SHOW STORAGE INFO;
CREATE SNAPSHOT;
