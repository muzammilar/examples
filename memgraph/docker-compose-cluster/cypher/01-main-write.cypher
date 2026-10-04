// Runs on memgraph-main. Payments between accounts, committed on MAIN and replicated.
MATCH (n) WHERE NOT n:Tick DETACH DELETE n;
CREATE INDEX ON :Account(id);
UNWIND range(1, 1000) AS i CREATE (:Account {id: i, balance: 100});
UNWIND range(1, 5000) AS i
MATCH (a:Account {id: (i * 7) % 1000 + 1}), (b:Account {id: (i * 13) % 1000 + 1})
CREATE (a)-[:PAID {amount: i % 50 + 1}]->(b);
MATCH (a:Account) WITH count(a) AS accounts MATCH ()-[p:PAID]->() RETURN accounts, count(p) AS payments, sum(p.amount) AS total;
SHOW REPLICAS;
