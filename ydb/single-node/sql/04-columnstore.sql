-- bulk-load 600 generated rows into the column table, then run an analytic scan over it
UPSERT INTO events
SELECT Unwrap(Timestamp("2026-01-01T00:00:00Z") + Interval("PT1M") * CAST(i AS Int64)) AS ts,
       Unwrap(["click"u, "view"u, "buy"u][i % 3u]) AS kind,
       CAST((i * 7u) % 13u AS Int64) AS value
FROM AS_TABLE(ListMap(ListFromRange(0u, 600u), ($i) -> (<|i: $i|>)));

SELECT kind, COUNT(*) AS n, SUM(value) AS total, MAX(value) AS max_value,
       MIN(ts) AS first_ts, MAX(ts) AS last_ts
FROM events GROUP BY kind ORDER BY kind;
