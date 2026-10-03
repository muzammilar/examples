-- DEDUP UPSERT KEYS: on a WAL table, a row whose (ts, key...) already exists replaces the old
-- row instead of adding a duplicate. That makes re-sending a batch (a retry after a timeout,
-- replaying a feed) idempotent. The keys must include the designated timestamp.
DROP TABLE IF EXISTS candles;
CREATE TABLE candles (
  symbol SYMBOL,
  close  DOUBLE,
  volume DOUBLE,
  ts     TIMESTAMP
) TIMESTAMP(ts) PARTITION BY DAY WAL
DEDUP UPSERT KEYS(ts, symbol);

INSERT INTO candles VALUES
  ('BTC-USD', 65010.5, 12.5, '2026-09-30T10:00:00Z'),
  ('ETH-USD',  2501.2, 80.0, '2026-09-30T10:00:00Z'),
  ('BTC-USD', 65100.0,  9.1, '2026-09-30T11:00:00Z');

-- the same 10:00 BTC candle again with a corrected close, plus an out-of-order older row
INSERT INTO candles VALUES
  ('BTC-USD', 65020.0, 12.7, '2026-09-30T10:00:00Z'),
  ('BTC-USD', 64990.0, 15.0, '2026-09-30T09:00:00Z');
