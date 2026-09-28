// Pattern match: friends-of-friends of Alice she doesn't already know.
MATCH (a:Person {name: "Alice"})-[:KNOWS]->()-[:KNOWS]->(fof)
WHERE NOT (a)-[:KNOWS]->(fof) AND fof <> a
RETURN DISTINCT fof.name AS suggestion;

// Variable-length path and shortest path.
MATCH p = (:Person {name: "Alice"})-[:KNOWS*1..3]->(v)
RETURN v.name AS reached, length(p) AS hops ORDER BY hops, reached;

MATCH p = shortestPath((:Person {name: "Alice"})-[:KNOWS*]-(:Person {name: "Dave"}))
RETURN [n IN nodes(p) | n.name] AS route, length(p) AS hops;
