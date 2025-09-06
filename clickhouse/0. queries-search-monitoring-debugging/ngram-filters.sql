/* Compute The ngram value of a column */
CREATE TABLE my_table (text_data String) ENGINE = Memory;
INSERT INTO my_table VALUES ('ClickHouse is fast.');
INSERT INTO my_table VALUES ('My DB is fast.');

/* Get all tri-grams FROM text_data column */
SELECT
    text_data,
    tokens(text_data) AS text_tokens,
    ngrams(text_data, 3) AS text_ngrams
FROM my_table;

/*.Get the count of all 3-grams */
SELECT
    uniq(text_data) text_num_strings,
    count() AS text_num_ngrams_all,
    uniq(text_ngrams_all) AS text_num_ngrams_uniq
FROM (
    SELECT
        /*groupArray(ngrams(text_data, 3)) AS text_ngrams_all*/
        text_data,
         arrayJoin(ngrams(text_data, 3)) AS text_ngrams_all
    FROM my_table
);

/* To Estimate the number of trigrams in a granule, we multiply the number of possible characters n-grams */
WITH
    52 AS possible_characters, /* 26 characters for lower case, 26 characters for upper case */
    3 AS num_grams
SELECT power(possible_characters,num_grams) AS all_combinations;

/* Create a Table with inverse index */
CREATE TABLE tab
(
    `key` UInt64,
    `str` String,
    INDEX inv_idx(str) TYPE text(tokenizer = 'ngram', ngram_size = 3, bloom_filter_false_positive_rate = 0.01) GRANULARITY 4
)
ENGINE = MergeTree
ORDER BY key
