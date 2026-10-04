-- EMIT ON WINDOW CLOSE: a window's row is written once, when the watermark passes the end of the
-- window, instead of being updated on every event. Needs a watermark, here on an append-only table.
-- A downstream watermark is the minimum over all parallel upstream actors, and DML rows of an
-- append-only table are spread round-robin over them: with 4 actors a window closes only after
-- every actor has seen a later row. One actor keeps the demo deterministic.
SET streaming_parallelism = 1;
CREATE TABLE trades (
    symbol VARCHAR,
    price  NUMERIC,
    ts     TIMESTAMP,
    WATERMARK FOR ts AS ts - INTERVAL '5 seconds'
) APPEND ONLY;

CREATE MATERIALIZED VIEW trades_1m_eowc AS
SELECT symbol, window_start, count(*) AS trades, max(price) AS high, min(price) AS low
FROM TUMBLE(trades, ts, INTERVAL '1 minute')
GROUP BY symbol, window_start
EMIT ON WINDOW CLOSE;

-- The same query without EOWC emits partial windows immediately.
CREATE MATERIALIZED VIEW trades_1m_live AS
SELECT symbol, window_start, count(*) AS trades, max(price) AS high, min(price) AS low
FROM TUMBLE(trades, ts, INTERVAL '1 minute')
GROUP BY symbol, window_start;

INSERT INTO trades VALUES
    ('ABC', 10.0, '2026-01-01 10:00:01'),
    ('ABC', 10.5, '2026-01-01 10:00:30'),
    ('ABC',  9.8, '2026-01-01 10:00:59');
FLUSH;
SELECT 'live' AS mv, * FROM trades_1m_live;
SELECT 'eowc' AS mv, * FROM trades_1m_eowc;   -- empty: watermark is 10:00:54, window ends 10:01:00

-- An event at 10:01:06 moves the watermark to 10:01:01 and closes the 10:00 window.
INSERT INTO trades VALUES ('ABC', 11.0, '2026-01-01 10:01:06');
FLUSH;
SELECT 'live' AS mv, * FROM trades_1m_live ORDER BY window_start;
SELECT 'eowc' AS mv, * FROM trades_1m_eowc ORDER BY window_start;   -- only the closed 10:00 window
SELECT 1 / (CASE WHEN (SELECT count(*) FROM trades_1m_eowc) = 1 THEN 1 ELSE 0 END) AS window_closed;

-- Events behind the watermark are dropped (late data): neither MV counts this one.
INSERT INTO trades VALUES ('ABC', 1.0, '2026-01-01 10:00:10');
FLUSH;
SELECT 1 / (CASE WHEN (SELECT trades FROM trades_1m_eowc WHERE window_start = '2026-01-01 10:00:00') = 3
              AND (SELECT trades FROM trades_1m_live WHERE window_start = '2026-01-01 10:00:00') = 3 THEN 1 ELSE 0 END) AS late_row_dropped;
SET streaming_parallelism = DEFAULT;
