
/* Insert random data into the base table */
INSERT INTO test.test_table_local SELECT
    now(),
    rand() % 97 /* Random numbers in range [0, 97) */
FROM system.numbers
LIMIT 500;

INSERT INTO test_replicated.test_rocks_local VALUES (1,3,'a',2);

INSERT INTO test_replicated.test_rocks VALUES (3,4,'c',54);

;;
