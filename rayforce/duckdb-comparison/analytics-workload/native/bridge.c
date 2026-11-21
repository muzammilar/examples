#include "bridge.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef ENGINE_RAYFORCE
#include <rayforce.h>
#include "core/pool.h"
static ray_runtime_t *runtime;
static const char *ray_message(void) {
    const char *message = ray_error_msg();
    return message ? message : "unknown error";
}
#else
#include <duckdb.h>
static duckdb_database database;
static duckdb_connection connection;
static duckdb_appender appender;
static duckdb_data_chunk append_chunk;
#endif
static char error[4096];
const char *bench_error(void) { return error; }
void bench_free(char *result) { free(result); }
int bench_open_file(int threads, const char *path) {
#ifdef ENGINE_RAYFORCE
    if (path) {
        snprintf(error, sizeof(error), "Rayforce requires explicit splayed/journal persistence APIs");
        return 1;
    }
    runtime = ray_runtime_create(0, NULL);
    if (!runtime || ray_pool_init_total((uint32_t)threads) != RAY_OK) {
        snprintf(error, sizeof(error), "Rayforce runtime/pool initialization failed");
        return 1;
    }
#else
    duckdb_config config;
    if (duckdb_create_config(&config) != DuckDBSuccess) return 1;
    char count[32];
    snprintf(count, sizeof(count), "%d", threads);
    duckdb_set_config(config, "threads", count);
    duckdb_set_config(config, "memory_limit", "3GB");
    char *reason = NULL;
    int failed = duckdb_open_ext(path, &database, config, &reason) != DuckDBSuccess;
    duckdb_destroy_config(&config);
    if (failed) {
        snprintf(error, sizeof(error), "%s", reason ? reason : "open failed");
        duckdb_free(reason);
        return 1;
    }
    if (duckdb_connect(database, &connection) != DuckDBSuccess) return 1;
#endif
    return 0;
}
int bench_open(int threads) { return bench_open_file(threads, NULL); }
char *bench_eval(const char *source, int materialize) {
    const size_t capacity = materialize ? 4 * 1024 * 1024 : 1;
    char *text = malloc(capacity);
    if (!text) { snprintf(error, sizeof(error), "result allocation failed"); return NULL; }
    text[0] = '\0';
    size_t used = 0;
    int failed = 0;
#ifdef ENGINE_RAYFORCE
    ray_t *result = ray_eval_str(source);
    if (!result || RAY_IS_ERR(result)) {
        snprintf(error, sizeof(error), "%s: %s", result ? ray_err_code(result) : "null",
                 ray_message());
        if (result) ray_error_free(result);
        free(text); return NULL;
    }
    int64_t rows = 0, columns = 0;
    if (materialize) {
        if (result->type != RAY_TABLE) {
            snprintf(error, sizeof(error), "query did not return a table (type %d)", result->type);
            ray_release(result); free(text); return NULL;
        }
        rows = ray_table_nrows(result); columns = ray_table_ncols(result);
    }
#else
    duckdb_result result;
    if (duckdb_query(connection, source, &result) != DuckDBSuccess) {
        snprintf(error, sizeof(error), "%s", duckdb_result_error(&result));
        duckdb_destroy_result(&result); free(text); return NULL;
    }
    int64_t rows = materialize ? (int64_t)duckdb_row_count(&result) : 0;
    int64_t columns = materialize ? (int64_t)duckdb_column_count(&result) : 0;
#endif
    for (int64_t row = 0; row < rows && !failed; row++) {
        for (int64_t col = 0; col < columns; col++) {
            int64_t value;
#ifdef ENGINE_RAYFORCE
            ray_t *column = ray_table_col_idx(result, col);
            if (!column || RAY_IS_ERR(column) || column->type != RAY_I64 || ray_vec_is_null(column, row)) {
                if (column && RAY_IS_ERR(column)) ray_error_free(column);
                failed = 1; break;
            }
            value = ray_vec_get_i64(column, row);
#else
            if (duckdb_value_is_null(&result, col, row)) { failed = 1; break; }
            value = duckdb_value_int64(&result, col, row);
#endif
            int n = snprintf(text + used, capacity - used, "%s%lld", col ? "," : "", (long long)value);
            if (n < 0 || (size_t)n >= capacity - used) { failed = 1; break; }
            used += (size_t)n;
        }
        if (!failed) {
            if (used + 2 >= capacity) { failed = 1; break; }
            text[used++] = ';'; text[used] = '\0';
        }
    }
#ifdef ENGINE_RAYFORCE
    ray_release(result);
#else
    duckdb_destroy_result(&result);
#endif
    if (failed) {
        snprintf(error, sizeof(error), "null/non-I64 result or result exceeds 4 MiB");
        free(text); return NULL;
    }
    return text;
}

static int append_trades(int64_t first_id, int64_t rows, int columnar) {
    if (first_id < 0 || rows < 1 || rows > 10000 || first_id > INT64_MAX - (rows - 1)) {
        snprintf(error, sizeof(error), "append requires nonnegative IDs and 1..10000 rows");
        return 1;
    }
    int64_t *values = malloc((size_t)rows * 6 * sizeof(int64_t));
    if (!values) { snprintf(error, sizeof(error), "append allocation failed"); return 1; }
    for (int64_t r = 0; r < rows; r++) {
        int64_t id = first_id + r;
        values[r] = id;
        values[rows + r] = id % 10000;
        values[2 * rows + r] = id % 256;
        values[3 * rows + r] = id % 30;
        values[4 * rows + r] = 1 + id % 100;
        values[5 * rows + r] = 100 + id % 9901;
    }
    int failed = 0;
#ifdef ENGINE_RAYFORCE
    (void)columnar; /* Both typed paths use the same Rayforce column API. */
    const char *names[] = {"id", "customer", "symbol", "day", "qty", "price"};
    ray_t *batch = ray_table_new(6);
    for (int col = 0; batch && !RAY_IS_ERR(batch) && col < 6; col++) {
        ray_t *vector = ray_vec_from_raw(RAY_I64, values + col * rows, rows);
        if (!vector || RAY_IS_ERR(vector)) {
            ray_release(batch); batch = vector; break;
        }
        batch = ray_table_add_col(batch, ray_sym_intern(names[col], strlen(names[col])), vector);
        ray_release(vector);
    }
    free(values);
    if (!batch || RAY_IS_ERR(batch)) {
        snprintf(error, sizeof(error), "Rayforce batch construction: %s", ray_message());
        if (batch) ray_error_free(batch);
        return 1;
    }
    /* Do not retain a view of trades: that disables its sole-owner append path.
     * The public C API supplies typed columns; only constant dispatch is parsed. */
    ray_err_t bound = ray_env_set(ray_sym_intern("_ingestion_batch", strlen("_ingestion_batch")), batch);
    ray_release(batch);
    if (bound != RAY_OK) {
        snprintf(error, sizeof(error), "Rayforce batch binding error %d", (int)bound);
        return 1;
    }
    ray_t *result = ray_eval_str("(insert 'trades _ingestion_batch)");
    if (!result || RAY_IS_ERR(result)) {
        snprintf(error, sizeof(error), "Rayforce native append: %s", ray_message());
        if (result) ray_error_free(result);
        return 1;
    }
    ray_release(result);
#else
    if (!appender && duckdb_appender_create(connection, NULL, "trades", &appender) != DuckDBSuccess) failed = 1;
    if (columnar && !failed) {
        if (!append_chunk) {
            duckdb_logical_type types[6];
            for (int c = 0; c < 6; c++) types[c] = duckdb_create_logical_type(DUCKDB_TYPE_BIGINT);
            append_chunk = duckdb_create_data_chunk(types, 6);
            for (int c = 0; c < 6; c++) duckdb_destroy_logical_type(&types[c]);
        }
        if (!append_chunk || !duckdb_vector_size()) {
            free(values);
            snprintf(error, sizeof(error), "DuckDB data chunk allocation failed");
            return 1;
        }
        for (idx_t offset = 0; offset < (idx_t)rows && !failed;) {
            idx_t count = (idx_t)rows - offset;
            if (count > duckdb_vector_size()) count = duckdb_vector_size();
            duckdb_data_chunk_reset(append_chunk);
            for (idx_t c = 0; c < 6; c++) {
                duckdb_vector vector = duckdb_data_chunk_get_vector(append_chunk, c);
                memcpy(duckdb_vector_get_data(vector), values + c * rows + offset,
                       (size_t)count * sizeof(int64_t));
            }
            duckdb_data_chunk_set_size(append_chunk, count);
            /* Pinned ColumnDataCollection::Append copies fixed-width values.
             * Reuse the chunk; flush only at the requested ACK boundary below. */
            failed = duckdb_append_data_chunk(appender, append_chunk) != DuckDBSuccess;
            offset += count;
        }
    } else {
        for (int64_t r = 0; r < rows && !failed; r++) {
            for (int c = 0; c < 6 && !failed; c++)
                failed = duckdb_append_int64(appender, values[c * rows + r]) != DuckDBSuccess;
            if (!failed) failed = duckdb_appender_end_row(appender) != DuckDBSuccess;
        }
    }
    free(values);
    /* A buffered append is not a visibility acknowledgement. Flush every batch. */
    if (!failed) failed = duckdb_appender_flush(appender) != DuckDBSuccess;
    if (failed) {
        duckdb_error_data data = appender ? duckdb_appender_error_data(appender) : NULL;
        const char *message = data ? duckdb_error_data_message(data) : NULL;
        snprintf(error, sizeof(error), "DuckDB Appender: %s", message ? message : "append failed");
        if (data) duckdb_destroy_error_data(&data);
    }
#endif
    return failed;
}

int bench_append(int64_t first_id, int64_t rows) {
    return append_trades(first_id, rows, 0);
}
int bench_append_columnar(int64_t first_id, int64_t rows) {
    return append_trades(first_id, rows, 1);
}

void bench_close(void) {
#ifdef ENGINE_RAYFORCE
    ray_pool_destroy();
    if (runtime) ray_runtime_destroy(runtime);
#else
    if (append_chunk) duckdb_destroy_data_chunk(&append_chunk);
    if (appender) duckdb_appender_destroy(&appender);
    if (connection) duckdb_disconnect(&connection);
    if (database) duckdb_close(&database);
#endif
}
