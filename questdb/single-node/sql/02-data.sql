-- Three days of synthetic market data generated in SQL: 2M quotes (one every ~130 ms) and
-- 1M trades (one every ~259 ms) over 3 symbols. long_sequence(n) yields n rows,
-- timestamp_sequence(start, step_micros) increasing timestamps, rnd_* random values. The mid
-- price follows a slow sine wave (a 2% swing every ~12.6 h) plus noise; quotes are 1 bp wide,
-- buys trade ~1 bp above the mid and sells ~1 bp below. Prices are whole cents / 100.0.
-- On WAL tables the INSERT returns once the rows are in the WAL; they become visible when the
-- WAL is applied (`make test` waits for that).
INSERT INTO quotes
SELECT symbol,
       round(mid * (1 - 0.00005) * 100) / 100.0 bid,
       round(mid * (1 + 0.00005) * 100) / 100.0 ask,
       ts
FROM (
  SELECT symbol, ts,
         CASE symbol WHEN 'BTC-USD' THEN 65000 WHEN 'ETH-USD' THEN 2500 ELSE 150 END
           * (1 + 0.01 * sin(ts::long / 7.2e9) + 0.0002 * (rnd_double() - 0.5)) mid
  FROM (SELECT rnd_symbol('BTC-USD', 'ETH-USD', 'SOL-USD') symbol,
               timestamp_sequence('2026-09-29T00:00:00.037000Z', 129_600L) ts
        FROM long_sequence(2_000_000))
);

INSERT INTO trades
SELECT symbol, side,
       round(CASE symbol WHEN 'BTC-USD' THEN 65000 WHEN 'ETH-USD' THEN 2500 ELSE 150 END
             * (1 + 0.01 * sin(ts::long / 7.2e9)
                  + CASE side WHEN 'buy' THEN 0.0001 ELSE -0.0001 END
                  + 0.0002 * (rnd_double() - 0.5)) * 100) / 100.0 price,
       rnd_long(1, 20000, 0) / 10000.0 amount,
       ts
FROM (SELECT rnd_symbol('BTC-USD', 'ETH-USD', 'SOL-USD') symbol, rnd_symbol('buy', 'sell') side,
             timestamp_sequence('2026-09-29T00:00:00.000000Z', 259_200L) ts
      FROM long_sequence(1_000_000));
