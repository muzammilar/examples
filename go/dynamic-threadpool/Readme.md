# Dynamic (Growing/Shrinking) Threadpool

A basic example of using Go routines to create a dynamically growing and shrinking worker pool (threadpool). For a fixed-size pool, see `../threadpool`.

The example consists of three packages:

* `pkg/worker`: A **worker** goroutine that pulls tasks (`func(context.Context)`) from a shared queue and uses a `select` statement to listen on channels so it can remove itself cleanly. Each worker has a **dedicated quit channel**, which allows the pool to deterministically remove a specific worker. A worker exits when:
  * its quit channel is closed (after finishing its current task),
  * the shared queue is closed and drained (graceful shutdown), or
  * the pool's context is cancelled (hard stop, the queue is not drained).
* `pkg/workerpool`: The **pool** that owns the workers and the task queue.
  * `Grow(n)`: Creates `n` new workers and adds them to the `sync.WaitGroup` of the workers.
  * `Shrink(n)`: Signals the `n` workers with the **highest worker IDs** to stop by closing their quit channels.
  * `Submit(ctx, task)`: Enqueues a task (blocks while the queue is full).
  * `Close()`: Stops accepting tasks, drains the queue and waits for every worker to exit.
  * The pool is bounded by `[Min, Max]` workers, and all methods are safe for concurrent use.
* `pkg/metrics`: Optional **Prometheus metrics** (see [Monitoring](#monitoring)). The other packages do not import Prometheus and nothing is registered globally: `metrics.New(reg, pool)` registers on the `prometheus.Registerer` you pass in.
* `pkg/controller`: A single **controller** goroutine that periodically monitors the queue depth and resizes the pool. The logic is intentionally primitive: grow by `Step` when more than `HighWater` tasks are queued, shrink by `Step` when at most `LowWater` tasks are queued.

The demo in `cmd/workerpool` produces tasks in alternating bursts and lulls (2 seconds each), so the pool can be seen growing and shrinking in the logs. It shuts down gracefully (draining the queue) when `-duration` elapses or on `Ctrl-C`. Use `-duration 0` to run until `SIGINT`/`SIGTERM`, `-phase` to change the burst/lull length and `-metrics-addr :8080` to serve Prometheus metrics on `/metrics` (disabled by default).

```sh
# run locally
go run ./cmd/workerpool -min 1 -max 16 -queue 64 -duration 10s
go run ./cmd/workerpool -h
# run forever with metrics on http://localhost:8080/metrics
go run ./cmd/workerpool -duration 0 -phase 20s -metrics-addr :8080

# run the tests (with the race detector)
go test -race ./...

# run the benchmarks (Submit throughput, Grow/Shrink cost, Submit while
# the controller is scaling, controller decision cost)
go test -bench=. -benchmem ./...
```

```sh
# build and start the demo, Prometheus and Grafana in the background
make docker-up        # docker compose up --build --detach
make docker-logs      # follow the demo's logs
# remove the containers, networks, volumes, orphans and the built image
# (the pulled prometheus/grafana images are kept)
make docker-down
```

## Monitoring

`docker-compose.yml` runs the demo continuously (`-duration=0`) with 20 second bursts and lulls (`-phase=20s`) so the pool visibly grows towards the maximum and shrinks back to the minimum, and exposes metrics on `:8080` inside the compose network only. Two more services are started:

| Service | Image | URL |
| --- | --- | --- |
| Prometheus | `prom/prometheus:v3.15.0` | <http://localhost:19115> (scrapes the demo every 5s, config in `prometheus/prometheus.yml`) |
| Grafana | `grafana/grafana:13.2.2` | <http://localhost:13015/d/dynamic-threadpool> (anonymous Admin, no login) |

Grafana is provisioned from `grafana/provisioning/` (mounted read-only): a `Prometheus` datasource (uid `prometheus`) and the **Dynamic Threadpool** dashboard (`dashboards/dynamic-threadpool.json`, refresh 5s, last 15 minutes) with these panels:

* **Pool size vs running workers**: `workerpool_size`, `workerpool_running_workers`, `workerpool_min_workers`, `workerpool_max_workers`
* **Queue length**: `workerpool_queue_length`
* **Scale events rate**: `sum by (direction) (rate(workerpool_scale_events_total[$__rate_interval]))`
* **Task throughput**: `rate(workerpool_tasks_submitted_total[...])` vs `rate(workerpool_tasks_processed_total[...])`
* **Task duration percentiles**: p50/p95/p99 of `workerpool_task_duration_seconds`

Metrics exported by `pkg/metrics` (plus the standard `go_*` and `process_*` collectors in the demo):

| Metric | Type | Description |
| --- | --- | --- |
| `workerpool_size` | gauge | Target number of workers (active, not stopped) |
| `workerpool_running_workers` | gauge | Worker goroutines that have not exited yet (briefly above size after a shrink) |
| `workerpool_min_workers` / `workerpool_max_workers` | gauge | Configured pool bounds |
| `workerpool_queue_length` | gauge | Tasks waiting in the queue |
| `workerpool_scale_events_total{direction="grow\|shrink"}` | counter | Successful resizes by the controller |
| `workerpool_tasks_submitted_total` | counter | Tasks accepted by `Submit` |
| `workerpool_tasks_processed_total` | counter | Tasks that finished running |
| `workerpool_task_duration_seconds` | histogram | Time spent running a task |

The gauges are read from `Size()`, `Running()`, `QueueLen()` and `Bounds()` by a custom `prometheus.Collector` on every scrape, scale events are counted by wrapping the controller's `Scaler` (`m.WrapScaler(pool)`), and tasks are counted by wrapping each task (`m.WrapTask(task)`).

## Make Targets

Run `make help` to list all targets. Unit tests and benchmarks are kept separate (plain `go test` skips `Benchmark*` functions, and `-run='^$'` skips the tests when benchmarking):

```sh
make test        # unit tests only
make test-race   # unit tests with the race detector
make bench       # benchmarks only; filter with BENCH=<regex>, tune with BENCH_TIME=2s BENCH_COUNT=5
make bench-cpu   # benchmarks at GOMAXPROCS 1, 4 and 8
make test-all    # vet + race tests + benchmarks
```
