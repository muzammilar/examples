UPSERT INTO kv (k, v) VALUES ("hello", "world"), ("foo", "bar");
SELECT * FROM kv ORDER BY k;
DELETE FROM kv WHERE k = "foo";
SELECT * FROM kv ORDER BY k;
