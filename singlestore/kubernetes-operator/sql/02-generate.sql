-- Generate the data server-side: a 6-way cross join of the 10-row digits reference table
-- yields 0..999999; every leaf produces and inserts its own share of rows in parallel.
USE demo;

INSERT INTO digits VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);
INSERT INTO regions VALUES (1, 'emea'), (2, 'americas'), (3, 'apac');

INSERT INTO customers
SELECT n, 1 + n % 3, CONCAT('user', n, '@example.com'), ELT(1 + (n % 10 > 6) + (n % 10 = 9), 'free', 'pro', 'enterprise')
FROM (SELECT a.n + 10 * b.n + 100 * c.n + 1000 * d.n + 10000 * e.n AS n
      FROM digits a, digits b, digits c, digits d, digits e) s
WHERE n < 50000;

INSERT INTO orders
SELECT n,
       (n * 7919) % 50000,
       ROUND(5 + RAND() * 495, 2),
       '2026-01-01' + INTERVAL n * 38 SECOND,
       JSON_BUILD_OBJECT('channel', ELT(1 + n % 3, 'web', 'store', 'app'),
                         'items', 1 + n % 5,
                         'coupon', IF(n % 11 = 0, 'SPRING', NULL))
FROM (SELECT a.n + 10 * b.n + 100 * c.n + 1000 * d.n + 10000 * e.n + 100000 * f.n AS n
      FROM digits a, digits b, digits c, digits d, digits e, digits f) s
WHERE n < 600000;

-- merge the in-memory write buffer into sorted columnstore segments, refresh statistics
OPTIMIZE TABLE orders FULL;
ANALYZE TABLE orders;
ANALYZE TABLE customers;
ANALYZE TABLE regions;

SELECT (SELECT COUNT(*) FROM customers) AS customers, (SELECT COUNT(*) FROM orders) AS orders,
       (SELECT MIN(created_at) FROM orders) AS first_order, (SELECT MAX(created_at) FROM orders) AS last_order;
