-- start fresh on every run
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS events;

-- row table: a synchronous GLOBAL secondary index on customer, a Json column,
-- and TTL (rows are deleted in the background 30 days after created_at)
CREATE TABLE orders (
    id Uint64 NOT NULL,
    customer Utf8,
    amount_cents Int64,
    created_at Timestamp,
    attrs Json,
    PRIMARY KEY (id),
    INDEX idx_customer GLOBAL ON (customer)
) WITH (TTL = Interval("P30D") ON created_at);

-- column-oriented (OLAP) table, hash-partitioned on its key
CREATE TABLE events (
    ts Timestamp NOT NULL,
    kind Utf8 NOT NULL,
    value Int64,
    PRIMARY KEY (ts, kind)
) PARTITION BY HASH(ts) WITH (STORE = COLUMN);
