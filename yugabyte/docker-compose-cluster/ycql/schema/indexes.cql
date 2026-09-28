-- Tables with secondary indexes for ycql/indexes.cql and ycql/jsonb.cql. `make test` runs
-- this first and waits a few seconds: a CQL proxy that uses a table while its new index is
-- still backfilling caches the index as not yet readable and keeps planning without it.
-- Indexes are tables of their own, kept in sync by a distributed transaction, so both the
-- table and its indexes need transactions enabled.
CREATE KEYSPACE IF NOT EXISTS demo;
USE demo;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS products;

CREATE TABLE orders (id int PRIMARY KEY, customer text, status text, amount int, note text)
  WITH transactions = {'enabled': true};
-- covering index: customer is the index key, status and amount are copied into the index
CREATE INDEX orders_by_customer ON orders (customer) INCLUDE (status, amount);

-- unique index: at most one customer per email
CREATE TABLE customers (id int PRIMARY KEY, email text, name text)
  WITH transactions = {'enabled': true};
CREATE UNIQUE INDEX customers_by_email ON customers (email);

-- JSONB documents with an index on one attribute
CREATE TABLE products (id int PRIMARY KEY, doc jsonb) WITH transactions = {'enabled': true};
CREATE INDEX products_by_brand ON products (doc->>'brand');
