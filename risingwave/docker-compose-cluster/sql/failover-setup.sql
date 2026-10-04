-- Tables written by loader/main.go during `make failover` and an MV per table.
DROP MATERIALIZED VIEW IF EXISTS events_no_flush_total;
DROP MATERIALIZED VIEW IF EXISTS events_flush_total;
DROP TABLE IF EXISTS events_no_flush;
DROP TABLE IF EXISTS events_flush;
CREATE TABLE events_no_flush (id BIGINT PRIMARY KEY, v INT);
CREATE TABLE events_flush (id BIGINT PRIMARY KEY, v INT);
CREATE MATERIALIZED VIEW events_no_flush_total AS SELECT count(*) AS n, sum(v) AS s FROM events_no_flush;
CREATE MATERIALIZED VIEW events_flush_total AS SELECT count(*) AS n, sum(v) AS s FROM events_flush;
