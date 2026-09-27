-- 100k customers and 3M orders generated server-side
\timing on
SET client_min_messages = warning; -- hide DROP ... IF EXISTS notices
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
CREATE TABLE customers (
  id      integer PRIMARY KEY,
  region  text NOT NULL,
  segment text NOT NULL,
  signup  date NOT NULL
);
CREATE TABLE orders (
  id          bigint PRIMARY KEY,
  customer_id integer NOT NULL REFERENCES customers,
  ordered_at  timestamp NOT NULL,
  status      text NOT NULL,
  amount      numeric(10,2) NOT NULL
);
INSERT INTO customers
SELECT i,
       (ARRAY['emea','amer','apac','latam'])[1 + (i % 4)],
       (ARRAY['consumer','smb','enterprise'])[1 + floor(random() * 3)::int],
       date '2020-01-01' + (random() * 2000)::int
FROM generate_series(1, 100000) AS g(i);
INSERT INTO orders
SELECT i,
       1 + floor(random() * 100000)::int,
       timestamp '2025-01-01' + random() * interval '365 days',
       (ARRAY['placed','shipped','delivered','returned'])[1 + floor(random() * 4)::int],
       round((5 + random() * random() * 995)::numeric, 2)
FROM generate_series(1, 3000000) AS g(i);
SELECT (SELECT count(*) FROM customers) AS customers, (SELECT count(*) FROM orders) AS orders;
