// Nodes and relationships in one pattern; MERGE keeps it idempotent.
UNWIND [["Alice", 34, "Berlin"], ["Bob", 28, "Paris"], ["Carol", 41, "Berlin"], ["Dave", 25, "Lisbon"]] AS row
MERGE (p:Person {name: row[0]}) SET p.age = row[1]
MERGE (c:City {name: row[2]})
MERGE (p)-[:LIVES_IN]->(c);

UNWIND [["Alice", "Bob", 2015], ["Bob", "Carol", 2019], ["Carol", "Dave", 2021], ["Alice", "Carol", 2020]] AS k
MATCH (a:Person {name: k[0]}), (b:Person {name: k[1]})
MERGE (a)-[r:KNOWS]->(b)
  ON CREATE SET r.since = k[2], r.created = true
  ON MATCH SET r.created = false
RETURN a.name, b.name, r.since, r.created;
