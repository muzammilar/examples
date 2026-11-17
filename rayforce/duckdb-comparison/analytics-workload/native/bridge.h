#ifndef BENCH_BRIDGE_H
#define BENCH_BRIDGE_H
#ifdef __cplusplus
extern "C" {
#endif
/* One engine/runtime per process; all calls run on its creating thread. */
int bench_open(int threads);
const char *bench_error(void);
char *bench_eval(const char *source, int materialize);
void bench_free(char *result);
void bench_close(void);
#ifdef __cplusplus
}
#endif
#endif
