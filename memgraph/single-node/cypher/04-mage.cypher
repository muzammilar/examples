// MAGE algorithms (query modules shipped in the memgraph-mage image)

// PageRank over the route graph: hubs with many incoming routes rank highest
CALL pagerank.get() YIELD node, rank
RETURN node.code AS airport, round(rank * 10000) / 10000 AS rank ORDER BY rank DESC LIMIT 5;

// Community detection (Louvain): should recover the three regions
CALL community_detection.get() YIELD node, community_id
RETURN community_id, collect(node.region)[0] AS region, count(*) AS airports,
       collect(node.code) AS members
ORDER BY community_id;

// Betweenness centrality: airports that sit on most shortest paths (bridges between regions)
CALL betweenness_centrality.get(TRUE, FALSE) YIELD node, betweenness_centrality
RETURN node.code AS airport, round(betweenness_centrality * 1000) / 1000 AS betweenness
ORDER BY betweenness DESC LIMIT 5;

// Weakly connected components: one component, every airport reachable
CALL weakly_connected_components.get() YIELD node, component_id
RETURN count(DISTINCT component_id) AS components, count(node) AS airports;
