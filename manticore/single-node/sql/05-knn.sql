-- Vector search: knn(field, k, vector) on the HNSW index; knn_dist() is the distance
-- (cosine: 0 = same direction). Vectors are hand-made: (running, outdoor, electronics, kitchen).
-- k is per HNSW index (per disk chunk) and not applied to the RAM chunk, so LIMIT sets the
-- number of results. Written in lower case: `KNN(embedding, 4, 6)` (upper case, document id)
-- fails to parse in 29.9.0, see README Known issues.

-- 1. nearest neighbours of "running gear for the outdoors"
SELECT id, title, knn_dist() AS dist FROM products WHERE knn(embedding, 5, (0.9, 0.6, 0.0, 0.0)) LIMIT 5;

-- 2. "more like product 6" (GPS watch): a document id instead of a vector
SELECT id, title, knn_dist() AS dist FROM products WHERE knn(embedding, 4, 6) LIMIT 4;

-- 3. KNN plus attribute filters (prefiltered inside the HNSW traversal by default)
SELECT id, title, price, knn_dist() AS dist FROM products
  WHERE knn(embedding, 3, (0.0, 0.2, 1.0, 0.0)) AND price < 300 LIMIT 3;

-- 4. KNN plus full-text: only documents matching 'waterproof', ordered by vector distance
SELECT id, title, knn_dist() AS dist FROM products
  WHERE knn(embedding, 10, (0.8, 0.6, 0.0, 0.0)) AND MATCH('waterproof') LIMIT 10;
