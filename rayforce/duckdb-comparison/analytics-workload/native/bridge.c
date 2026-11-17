#include "bridge.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef ENGINE_RAYFORCE
#include <rayforce.h>
#include "core/pool.h"
static ray_runtime_t *runtime;
#else
#include <duckdb.h>
static duckdb_database database;
static duckdb_connection connection;
#endif
static char error[4096];
const char *bench_error(void) { return error; }
void bench_free(char *result) { free(result); }
int bench_open(int threads) {
#ifdef ENGINE_RAYFORCE
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
    int failed = duckdb_open_ext(NULL, &database, config, &reason) != DuckDBSuccess;
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
char *bench_eval(const char *source, int materialize) {
    const size_t capacity = 4 * 1024 * 1024;
    char *text = malloc(capacity);
    if (!text) { snprintf(error, sizeof(error), "result allocation failed"); return NULL; }
    text[0] = '\0';
    size_t used = 0;
    int failed = 0;
#ifdef ENGINE_RAYFORCE
    ray_t *result = ray_eval_str(source);
    if (!result || RAY_IS_ERR(result)) {
        snprintf(error, sizeof(error), "%s: %s", result ? ray_err_code(result) : "null",
                 ray_error_msg());
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
void bench_close(void) {
#ifdef ENGINE_RAYFORCE
    ray_pool_destroy();
    if (runtime) ray_runtime_destroy(runtime);
#else
    if (connection) duckdb_disconnect(&connection);
    if (database) duckdb_close(&database);
#endif
}
