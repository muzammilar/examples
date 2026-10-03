-- ASOF JOIN: each trade joins the most recent quote at or before its timestamp, for the same
-- symbol (ON). Both sides are ordered by their designated timestamp, so it is a merge, not a
-- nested loop. LT JOIN is the same but strictly before; SPLICE JOIN interleaves both sides.

-- every trade with the prevailing quote
SELECT t.ts, t.symbol, t.side, t.price, q.bid, q.ask, q.ts quote_ts
FROM trades t ASOF JOIN quotes q ON (symbol)
WHERE t.ts IN '2026-09-30T12:00:00;1s'
ORDER BY t.ts;

-- execution quality: average distance of the trade price from the quote mid, in basis points
SELECT t.symbol, t.side,
       round(avg((t.price - (q.bid + q.ask) / 2) / ((q.bid + q.ask) / 2) * 10000), 2) avg_bps,
       count() trades
FROM trades t ASOF JOIN quotes q ON (symbol)
WHERE t.ts IN '2026-09-30'
GROUP BY t.symbol, t.side
ORDER BY t.symbol, t.side;

-- TOLERANCE: ignore quotes older than 300 ms (the quote columns are NULL then)
SELECT count() trades, count(q.bid) with_fresh_quote
FROM trades t ASOF JOIN quotes q ON (symbol) TOLERANCE 300T
WHERE t.ts IN '2026-09-30';
