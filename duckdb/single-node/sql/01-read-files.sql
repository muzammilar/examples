-- query raw files in place: types, delimiters and headers are sniffed automatically
.timer on
SELECT count(*) AS events FROM 'events.csv';

SELECT column_name, column_type FROM (DESCRIBE FROM read_csv('events.csv'));
-- NDJSON: the tags array becomes VARCHAR[], the prefs object a STRUCT
SELECT column_name, column_type FROM (DESCRIBE FROM read_json('users.json'));

-- per-column min/max/approx distinct/null % over 1M rows
SELECT column_name, column_type, min, max, approx_unique, null_percentage
FROM (SUMMARIZE 'events.csv');

-- views over the files (kept in demo.duckdb, so `make cli` has them too)
CREATE OR REPLACE VIEW events AS FROM read_csv('events.csv');
CREATE OR REPLACE VIEW users AS FROM read_json('users.json');
CREATE OR REPLACE VIEW countries AS FROM 'countries.csv';
