/* Generate 1000 data points between start_time and start_time + num_days */
WITH
    '2025-01-01 00:00:00' as start_time,
    90 as num_days,
    1000 as num_data_points
SELECT
    toDateTime(start_time) + toInt64(randUniform(0, ((60 * 60) * 24) * num_days)) AS random_datetime
FROM system.numbers
LIMIT num_data_points


/* Generate a histogram to understand the distribution of the data */
WITH
    ipv4_addr_string as col_name,
    256 as number_of_bins
SELECT histogram(number_of_bins)(sipHash64(col_name)) as my_histogram
FROM
WHERE server_timestamp > now() - toIntervalYear(3);

/* Generate random data for a table */
WITH
    'id UInt64, name String, value Float64' as table_schema,
    123 as seed,
    3 as max_string_length,
    5 as max_array_length,
    100 as num_rows
SELECT * FROM generateRandom(table_schema, seed, max_string_length, max_array_length) LIMIT num_rows;

/* Generate random data for a table - with a schema - You can do CREATE TABLE my_new_table AS my_other_table */
/**/
CREATE TABLE my_random_table (
    id UInt64,
    name String,
    value Float64
ENGINE = GenerateRandom();
-- GenerateRandom([random_seed [,max_string_length [,max_array_length]]])


/* randChiSquared() - This is primarily used for testing statistical hypotheses - specifically whether a dataset matches a distribution.  */
SELECT
    floor(randChiSquared(10)) AS k,
    bar(count(*), 0, 10000, 10) AS b1
FROM numbers(100000)
GROUP BY k
ORDER BY k ASC

/* Multi-Modal Binomial */
SELECT
    floor(randBinomial(24, 0.75)) AS k,
    count(*) AS c,
    number % 3 AS ord,
    bar(c, 0, 10000)
FROM numbers(100000)
GROUP BY
    k,
    ord
ORDER BY
    ord ASC,
    k ASC
