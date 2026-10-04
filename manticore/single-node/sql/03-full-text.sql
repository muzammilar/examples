-- Full-text search: MATCH() takes the Manticore query language; WEIGHT() is the relevance
-- score. The default ranker (proximity_bm25) adds phrase proximity to BM25; ranker=bm25 is
-- plain BM25. Stemming makes 'run' match "running", "runs", "runners".

-- 1. plain keywords, default ranker vs pure BM25
SELECT id, title, WEIGHT() AS w FROM products WHERE MATCH('trail running') ORDER BY w DESC, id ASC;
SELECT id, title, WEIGHT() AS w FROM products WHERE MATCH('trail running') ORDER BY w DESC, id ASC OPTION ranker=bm25;

-- 2. BM25F with field weights: a hit in title counts 5x a hit in description
SELECT id, title, WEIGHT() AS w FROM products WHERE MATCH('waterproof')
  ORDER BY w DESC, id ASC OPTION ranker=expr('10000*bm25f(1.2,0.75,{title=5,description=1})');

-- 3. operators: field limit (@title), phrase ("..."), proximity ("..."~N), OR (|), NOT (-)
SELECT id, title FROM products WHERE MATCH('@title headphones -noise');
SELECT id, title FROM products WHERE MATCH('"trail shoes"');
SELECT id, title FROM products WHERE MATCH('"shoes trails"~6');
SELECT id, title FROM products WHERE MATCH('(stove | skillet) camp*');

-- 4. infix wildcard (min_infix_len) and fuzzy matching (typos; handled by Buddy)
SELECT id, title FROM products WHERE MATCH('*proof*');
-- fuzzy compares against the dictionary (stemmed words here), up to 2 edits by default
SELECT id, title FROM products WHERE MATCH('runing jaket') OPTION fuzzy=1;

-- 5. highlighting: HIGHLIGHT() returns the matched fields with <b> tags; options set the
--    tags, the snippet length and which fields to use
SELECT id, HIGHLIGHT() AS hl FROM products WHERE MATCH('waterproof trail');
SELECT id, HIGHLIGHT({before_match='[', after_match=']', limit=40}, 'description') AS snippet
  FROM products WHERE MATCH('running');

-- 6. full-text plus attribute filters and sorting by an attribute
SELECT id, title, price, rating FROM products
  WHERE MATCH('running') AND price < 200 AND ANY(tags) = 2 ORDER BY rating DESC;

-- 7. SHOW META after a query: per-keyword document and hit counts, time
SELECT id FROM products WHERE MATCH('running trail');
SHOW META;
