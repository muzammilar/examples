-- LATEST ON: the newest row per key, read backwards from the end of the table.

-- current top of book for every symbol
SELECT symbol, bid, ask, ts FROM quotes LATEST ON ts PARTITION BY symbol ORDER BY symbol;

-- as of a point in time: the filter is applied first, then the latest row per key
SELECT symbol, bid, ask, ts FROM quotes
WHERE ts < '2026-09-30T09:30:00'
LATEST ON ts PARTITION BY symbol ORDER BY symbol;

-- the last trade per (symbol, side)
SELECT symbol, side, price, amount, ts FROM trades
LATEST ON ts PARTITION BY symbol, side ORDER BY symbol, side;
