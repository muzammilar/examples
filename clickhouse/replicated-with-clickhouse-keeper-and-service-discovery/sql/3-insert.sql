
/* Insert random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand() % 97 /* Random numbers in range [0, 97) */
FROM system.numbers
LIMIT 500;


;;
