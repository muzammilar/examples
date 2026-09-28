// Read back, in a read transaction (cypher-shell --access-mode read): routed to a follower.
MATCH (p:Person)-[:LIVES_IN]->(c:City)
RETURN c.name AS city, collect(p.name) AS people ORDER BY city;

MATCH path = (:Person {name: "Alice"})-[:KNOWS*]->(:Person {name: "Carol"})
RETURN [n IN nodes(path) | n.name] AS route;
