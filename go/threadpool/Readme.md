# Static ThreadPool

A basic example of a static (fixed-size) worker pool in Go. A fixed number of
worker goroutines are started up front and pull tasks from a bounded queue.
Producers block in `Submit` when the queue is full, which gives natural
backpressure without spawning a goroutine per task.

* `pool.go`: the `Pool` type (`New`, `Submit`, `Close`).
    * `Submit(ctx, task)` blocks while the queue is full; it returns `ctx.Err()` if the context ends first, or `ErrPoolClosed` after `Close`.
    * `Close()` stops accepting tasks, drains the queue, and waits for all workers to exit. It is safe to call more than once.
    * `New(size, queue, WithObserver(o))` reports submit and task events to an `Observer` (e.g. for metrics). Without it the pool has no metrics overhead and no dependency on Prometheus.
* `threadpool.go`: a demo where several producers submit messages, workers checksum them, and `main` aggregates per-worker counts.
* `metrics.go`: `PromObserver`, a Prometheus `Observer` used by the demo. It registers on a private registry, not the global one.

## Run

```sh
go run . -workers 8 -queue 64 -producers 4 -messages 100000
go test -race ./...
```

By default the demo is one-shot: it processes `-messages` messages as fast as it can and exits. Other flags:

* `-rate N`: continuous mode. Produce N messages/sec until SIGINT/SIGTERM (or for `-duration`, e.g. `-duration 30s`; `0` means until a signal). On a signal it stops producing, drains the queue and prints the per-worker summary.
* `-task-time D`: extra simulated work per task, random in `[D/2, 3D/2)` (default `0`, checksum only).
* `-metrics-addr ADDR`: serve Prometheus metrics on `ADDR/metrics` (default empty, disabled).

```sh
go run . -rate 1000 -duration 0 -task-time 5ms -metrics-addr :8080   # or: make run-continuous
curl -s localhost:8080/metrics | grep ^threadpool_
```

### Metrics

| Metric | Type | Description |
|---|---|---|
| `threadpool_tasks_submitted_total` | counter | Tasks accepted by `Submit` |
| `threadpool_tasks_processed_total{worker}` | counter | Tasks completed, per worker |
| `threadpool_queue_depth` | gauge | Tasks waiting in the queue |
| `threadpool_queue_capacity` | gauge | Queue capacity (`-queue`) |
| `threadpool_busy_workers` | gauge | Workers currently running a task |
| `threadpool_workers` | gauge | Fixed pool size (`-workers`) |
| `threadpool_task_duration_seconds` | histogram | Time a worker spent running a task |
| `threadpool_submit_wait_seconds` | histogram | Time `Submit` blocked before the task was queued |

Go runtime (`go_*`) and process (`process_*`) metrics are exported too.

## Benchmarks

```sh
go test -bench=. -benchmem ./...
# benchmarks only, skipping the unit tests
go test -run='^$' -bench=. -benchmem ./...
```

* `BenchmarkPoolSubmit`: single-producer `Submit` throughput (no-op task) for pool sizes 1/4/16/`GOMAXPROCS` and queue sizes 0/64/1024.
* `BenchmarkPoolSubmitParallel`: `Submit` under contention from `GOMAXPROCS` producers.
* `BenchmarkDemoWorkload`: the demo end to end (message generation, checksum, result aggregation); one op is one message.

An unbuffered queue (`queue=0`) is noticeably slower because every `Submit` has to hand off directly to an idle worker.

## Docker

```sh
make up     # docker compose up --build --detach, then waits until Grafana is ready
make logs   # follow the app logs
make down   # remove containers, networks, volumes, orphans and the built threadpool image
```

The compose stack runs the demo in continuous mode (1200 msgs/sec, `-task-time 5ms`, 8 workers, metrics on `:8080` inside the compose network only), plus Prometheus and Grafana. `make down` keeps the pulled `prom/prometheus` and `grafana/grafana` images.

## Monitoring

* Grafana: <http://localhost:13013/d/threadpool-static> (anonymous admin, no login)
* Prometheus: <http://localhost:19113> (scrapes `threadpool:8080` every 5s; targets at <http://localhost:19113/targets>)

Grafana is provisioned from `grafana/provisioning/` with a `Prometheus` datasource (uid `prometheus`) and the **Static ThreadPool** dashboard (`grafana/provisioning/dashboards/threadpool.json`, refresh 5s, last 15 minutes). Panels:

* **Throughput per worker**: `rate(threadpool_tasks_processed_total[1m])` per worker (stacked), the total processed rate, and the total submitted rate. With a fixed pool the per-worker rates should be about equal.
* **Queue depth**: `threadpool_queue_depth` against `threadpool_queue_capacity`. When depth reaches capacity, producers block.
* **Busy workers vs pool size**: `threadpool_busy_workers` against `threadpool_workers`. Busy workers pinned at pool size means the pool is saturated.
* **Task duration p50 / p95 / p99**: quantiles of `threadpool_task_duration_seconds`.
* **Submit wait p95**: 95th percentile of `threadpool_submit_wait_seconds`, i.e. how long producers waited for space in the queue (backpressure).

## Make Targets

Run `make help` to list all targets. Unit tests and benchmarks are kept separate (plain `go test` skips `Benchmark*` functions, and `-run='^$'` skips the tests when benchmarking):

```sh
make test        # unit tests only
make test-race   # unit tests with the race detector
make bench       # benchmarks only; filter with BENCH=<regex>, tune with BENCH_TIME=2s BENCH_COUNT=5
make bench-cpu   # benchmarks at GOMAXPROCS 1, 4 and 8
make test-all    # vet + race tests + benchmarks
```
