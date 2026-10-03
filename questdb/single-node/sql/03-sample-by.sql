-- SAMPLE BY: time-bucketed aggregation over the designated timestamp.
-- `ts IN '2026-09-30'` is an interval filter: only that day's partition is opened.

-- hourly OHLCV candles for one symbol on one day
SELECT ts, first(price) open, max(price) high, min(price) low, last(price) close,
       sum(amount)::decimal(18,4) volume, count() trades
FROM trades
WHERE symbol = 'BTC-USD' AND ts IN '2026-09-30'
SAMPLE BY 1h
LIMIT 4;

-- 250 ms buckets (250T; T = millisecond) are finer than one symbol's trade rate, so some are
-- empty: FILL(PREV) carries the last close forward (FILL(NULL), FILL(LINEAR) and FILL(<value>)
-- are the alternatives; a filled count() would repeat too)
SELECT ts, last(price) close
FROM trades
WHERE symbol = 'BTC-USD' AND ts IN '2026-09-30T12:00:00;3s'
SAMPLE BY 250T FILL(PREV)
LIMIT 12;

-- daily VWAP per symbol (SAMPLE BY + GROUP BY key)
SELECT ts, symbol, (sum(price * amount) / sum(amount))::decimal(18,2) vwap, count() trades
FROM trades
SAMPLE BY 1d
ORDER BY ts, symbol;

-- the plan: an interval forward scan over one partition instead of a full scan
EXPLAIN SELECT ts, last(price) FROM trades WHERE ts IN '2026-09-30' SAMPLE BY 1h;
