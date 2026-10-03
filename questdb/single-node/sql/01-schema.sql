-- Time-series tables: a designated timestamp (rows are stored sorted by it), daily
-- partitions (one directory per day; queries on a time range only open those days) and WAL
-- (writes go to a write-ahead log and are applied asynchronously, so many connections can
-- write to one table at once, out-of-order rows are merged in, and DEDUP works).
DROP TABLE IF EXISTS trades;
DROP TABLE IF EXISTS quotes;

CREATE TABLE trades (
  symbol SYMBOL CAPACITY 256,  -- SYMBOL: interned string, stored as an int
  side   SYMBOL,
  price  DOUBLE,
  amount DOUBLE,
  ts     TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;

CREATE TABLE quotes (
  symbol SYMBOL CAPACITY 256,
  bid    DOUBLE,
  ask    DOUBLE,
  ts     TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL;

SELECT table_name, designatedTimestamp, partitionBy, walEnabled, dedup
FROM tables() WHERE table_name IN ('trades', 'quotes') ORDER BY 1;
