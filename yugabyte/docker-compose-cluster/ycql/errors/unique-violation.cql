-- expect: Duplicate value disallowed by unique index customers_by_email
-- A second customer with alice's email: rejected by the unique index from ycql/indexes.cql.
INSERT INTO demo.customers (id, email, name) VALUES (3, 'alice@example.com', 'mallory');
