CREATE TABLE customers (
  id BIGINT PRIMARY KEY, region TEXT NOT NULL, revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 999999)
);
CREATE TABLE orders (
  id BIGINT PRIMARY KEY, customer_id BIGINT NOT NULL REFERENCES customers(id),
  amount_cents BIGINT NOT NULL, revision BIGINT NOT NULL CHECK (revision BETWEEN 1 AND 1000000),
  cancelled BOOLEAN NOT NULL, event_second BIGINT NOT NULL
);
CREATE TABLE receipts (
  id TEXT PRIMARY KEY, operation TEXT NOT NULL, first_id BIGINT NOT NULL,
  last_id BIGINT NOT NULL, affected BIGINT NOT NULL, committed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE customers REPLICA IDENTITY FULL;
ALTER TABLE orders REPLICA IDENTITY FULL;
CREATE PUBLICATION pipeline FOR TABLE public.customers, public.orders;
CREATE ROLE app LOGIN PASSWORD 'local-app-only';
GRANT USAGE ON SCHEMA public TO app;
GRANT SELECT, INSERT, UPDATE, DELETE ON customers, orders, receipts TO app;
CREATE ROLE cdc LOGIN REPLICATION PASSWORD 'local-cdc-only';
GRANT USAGE ON SCHEMA public TO cdc;
GRANT SELECT ON customers, orders TO cdc;
