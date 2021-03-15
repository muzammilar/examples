
/* Insert random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand(),
    rand()
FROM system.numbers
LIMIT 500;

/* Sleep 1 second */
SELECT sleep(1);

/* Insert more random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand(),
    rand()
FROM system.numbers
LIMIT 10000;

/* Sleep 2 second */
SELECT sleep(2);

/* Insert more random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand(),
    rand()
FROM system.numbers
LIMIT 200;

/* Sleep 1 second */
SELECT sleep(1);

/* Insert more random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand(),
    rand()
FROM system.numbers
LIMIT 1000;

;;
