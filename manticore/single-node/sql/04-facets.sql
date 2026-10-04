-- Faceted search: one statement returns the hits plus a result set per FACET, all computed
-- over the same full-text match in one pass (one round trip from the application).
SELECT id, title, brand, price FROM products WHERE MATCH('running | trail')
  ORDER BY WEIGHT() DESC, id ASC LIMIT 5
  FACET category ORDER BY COUNT(*) DESC
  FACET brand ORDER BY COUNT(*) DESC, brand ASC LIMIT 3
  FACET INTERVAL(price, 100, 200, 400) AS price_range ORDER BY price_range ASC
  FACET tags ORDER BY COUNT(*) DESC;

-- plain GROUP BY with aggregates
SELECT category, COUNT(*) AS n, MIN(price) AS min_price, MAX(price) AS max_price,
       AVG(rating) AS avg_rating, SUM(stock) AS stock
  FROM products GROUP BY category ORDER BY n DESC, category ASC;

-- top product per category (GROUP N BY would keep N per group)
SELECT category, id, title, rating FROM products GROUP BY category
  WITHIN GROUP ORDER BY rating DESC ORDER BY category ASC;
