-- Real-time (RT) table: rows are searchable as soon as INSERT returns. Text fields are
-- tokenized into an inverted index; attributes (string, float, int, multi, timestamp,
-- float_vector) are stored per row for filters, sorting, grouping and KNN.
--   morphology='stem_en'  English stemming: "shoes" matches "shoe", "running" matches "run"
--   min_infix_len='3'      infix index for wildcard ('*proof*') and fuzzy search
--   embedding              4 hand-made dimensions (running, outdoor, electronics, kitchen) with an
--                          HNSW index for KNN; real setups use model output (384+ dims)
DROP TABLE IF EXISTS products;
CREATE TABLE products (
  title text,
  description text,
  category string,
  brand string,
  price float,
  rating float,
  stock int,
  tags multi,
  added timestamp,
  embedding float_vector knn_type='hnsw' knn_dims='4' hnsw_similarity='cosine'
) morphology='stem_en' min_infix_len='3';

DESC products;
SHOW CREATE TABLE products;
