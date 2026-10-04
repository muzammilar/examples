// Index lookup and 1-hop neighbours
MATCH (a:Airport {code: 'FRA'})-[r:ROUTE]->(b) RETURN b.code AS to, r.km AS km ORDER BY km;

// Variable-length expansion: airports reachable from SEA in at most 2 flights
MATCH (:Airport {code: 'SEA'})-[:ROUTE *1..2]->(b) RETURN count(DISTINCT b) AS within_2_flights;

// Breadth-first search (fewest flights), built-in *BFS expansion of Memgraph
MATCH p = (:Airport {code: 'MAD'})-[:ROUTE *BFS]->(:Airport {code: 'BKK'})
RETURN [n IN nodes(p) | n.code] AS route, size(relationships(p)) AS flights;

// Weighted shortest path (fewest km), built-in *WSHORTEST with a lambda for the weight
MATCH p = (:Airport {code: 'MAD'})-[:ROUTE *WSHORTEST (r, n | r.km) total_km]->(:Airport {code: 'BKK'})
RETURN [n IN nodes(p) | n.code] AS route, total_km;

// BFS with a filter lambda, no single flight longer than 8000 km (skips the direct FRA-SIN)
MATCH p = (:Airport {code: 'FRA'})-[:ROUTE *BFS (r, n | r.km < 8000)]->(:Airport {code: 'SIN'})
RETURN [n IN nodes(p) | n.code] AS short_hops_only;

// Aggregation: routes and average distance per region. The EXPLAIN after it shows the
// label+property index scan (no comment may precede EXPLAIN or PROFILE, see README)
MATCH (a:Airport)-[r:ROUTE]->(b:Airport)
RETURN a.region AS region, count(r) AS routes,
       sum(CASE WHEN a.region <> b.region THEN 1 ELSE 0 END) AS long_haul,
       round(avg(r.km)) AS avg_km
ORDER BY region;

EXPLAIN MATCH (a:Airport {code: 'FRA'}) RETURN a;
