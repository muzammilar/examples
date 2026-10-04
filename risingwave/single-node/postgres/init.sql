-- Target of the `revenue_to_postgres` sink (sql/04-sinks.sql).
CREATE TABLE revenue_by_region (
    region  VARCHAR PRIMARY KEY,
    orders  BIGINT,
    revenue NUMERIC
);
