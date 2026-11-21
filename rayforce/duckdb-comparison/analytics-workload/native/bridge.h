#ifndef BENCH_BRIDGE_H
#define BENCH_BRIDGE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* One engine/runtime per process; all calls run on its creating thread. */
int bench_open(int threads);
/* DuckDB persistent file, or NULL for the existing in-memory profile.
 * Rayforce uses explicit splayed/journal APIs and rejects a non-NULL path. */
int bench_open_file(int threads, const char *path);
const char *bench_error(void);
char *bench_eval(const char *source, int materialize);
/* Append deterministic six-I64 trades and make this batch query-visible in RAM. */
int bench_append(int64_t first_id, int64_t rows);
/* DuckDB data-chunk Appender; Rayforce uses the same typed-column path. */
int bench_append_columnar(int64_t first_id, int64_t rows);
void bench_free(char *result);
void bench_close(void);
#ifdef __cplusplus
}
#endif
#endif
