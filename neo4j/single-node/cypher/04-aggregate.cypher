// Aggregation: people per city, average age, and a collected list.
MATCH (p:Person)-[:LIVES_IN]->(c:City)
RETURN c.name AS city, count(p) AS people, avg(p.age) AS avg_age, collect(p.name) AS names
ORDER BY people DESC, city;
