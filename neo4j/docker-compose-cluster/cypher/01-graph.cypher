// A small graph, written through the routing driver (neo4j://): the write goes to the leader.
CREATE CONSTRAINT person_name IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE;

UNWIND [["Alice", "Berlin"], ["Bob", "Paris"], ["Carol", "Berlin"]] AS row
MERGE (p:Person {name: row[0]})
MERGE (c:City {name: row[1]})
MERGE (p)-[:LIVES_IN]->(c);

UNWIND [["Alice", "Bob"], ["Bob", "Carol"]] AS k
MATCH (a:Person {name: k[0]}), (b:Person {name: k[1]})
MERGE (a)-[:KNOWS]->(b);
