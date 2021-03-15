
/* Compute hourly entropy from the insert table directly */
SELECT
    insert_timestamp,
    entropy(val32)
FROM test.test_table_local
GROUP BY toStartOfHour(insert_timestamp) AS insert_timestamp;

/* Compute daily entropy from the insert table directly */
SELECT
    insert_timestamp,
    entropy(val32)
FROM test.test_table_local
GROUP BY toStartOfDay(insert_timestamp) AS insert_timestamp;


/* Compute entropy from the insert table directly with intermediate state */
SELECT insert_timestamp_hr, entropyMerge(entrop_intermediate_state) as entropy_final_state
FROM
(
    SELECT insert_timestamp_hr, entropyState(val64) AS entrop_intermediate_state
    FROM test.test_table_local
    GROUP BY toStartOfHour(insert_timestamp) AS insert_timestamp_hr
)
GROUP BY insert_timestamp_hr;

/* Compute entropy from the hourly AMT */
SELECT
    insert_timestamp,
    entropyMerge(val32_es) AS val32_entropy,
    entropyMerge(val64_es) AS val64_entropy
FROM test.test_table_hourly_amt_local
GROUP BY insert_timestamp;

/* Compute entropy from the hourly AMT using a View */
SELECT * FROM test_views.test_table_hourly_entropy_local;

/* Compute entropy from the daily AMT using a View */
SELECT * FROM test_views.test_table_daily_entropy_local;

/* Get the entropy of latest batched insert */
SELECT * FROM test_results.test_table_entropy_batch_insert_latest_local FINAL;

/* Get the entropy of each batched insert */
SELECT * FROM test_results.test_table_entropy_batch_insert_all_local;

;;
