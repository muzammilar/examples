-- per-write TTL, TTL()/WRITETIME() metadata, and a clustering-key range scan inside one partition
CONSISTENCY ONE;
INSERT INTO demo.readings (sensor, ts, value) VALUES ('s1', '2026-01-01 10:00:00+0000', 20.5);
INSERT INTO demo.readings (sensor, ts, value) VALUES ('s1', '2026-01-01 11:00:00+0000', 21.0) USING TTL 3600;
INSERT INTO demo.readings (sensor, ts, value) VALUES ('s1', '2026-01-01 12:00:00+0000', 22.5);
INSERT INTO demo.readings (sensor, ts, value) VALUES ('s1', '2026-01-01 13:00:00+0000', 23.0);
SELECT ts, value, TTL(value), WRITETIME(value) FROM demo.readings
  WHERE sensor = 's1' AND ts > '2026-01-01 10:00:00+0000' AND ts <= '2026-01-01 12:00:00+0000';
