
/* Get Results from inserts */

SELECT * from test.test_table;

SELECT count() from test.test_table;

SELECT uniq(val), uniqCombined(val) from test.test_table;

SELECT * FROM test_replicated.test_rocks_local;

SELECT * FROM test_replicated.test_rocks;

;;
