
/* Delete one entry */
DELETE FROM test.test_table WHERE key = 6;

/* Update one entry with a new timestamp */
INSERT INTO test.test_table (key, val, data_timestamp) VALUES (3, pow(3, 3), now()+toIntervalDay(1));

/* Update one entry with an old timestamp */
DELETE FROM test.test_table WHERE key = 9;
INSERT INTO test.test_table (key, val, data_timestamp) VALUES (9, pow(9, 3), now() - toIntervalDay(1));

/* Optimize the table - Don't do this in production */
OPTIMIZE TABLE test.test_table;

;;
