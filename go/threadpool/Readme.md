# Static ThreadPool

A basic example of a static (fixed-size) worker pool in Go. A fixed number of
worker goroutines are started up front and pull tasks from a bounded queue.
Producers block in `Submit` when the queue is full, which gives natural
backpressure without spawning a goroutine per task.

* `pool.go`: the `Pool` type (`New`, `Submit`, `Close`).
    * `Submit(ctx, task)` blocks while the queue is full; it returns `ctx.Err()` if the context ends first, or `ErrPoolClosed` after `Close`.
    * `Close()` stops accepting tasks, drains the queue, and waits for all workers to exit. It is safe to call more than once.
* `threadpool.go`: a demo where several producers submit messages, workers checksum them, and `main` aggregates per-worker counts.

## Run

```sh
go run . -workers 8 -queue 64 -producers 4 -messages 100000
go test -race ./...
```

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
docker compose build
docker compose up
```

## Make Targets

Run `make help` to list all targets. Unit tests and benchmarks are kept separate (plain `go test` skips `Benchmark*` functions, and `-run='^$'` skips the tests when benchmarking):

```sh
make test        # unit tests only
make test-race   # unit tests with the race detector
make bench       # benchmarks only; filter with BENCH=<regex>, tune with BENCH_TIME=2s BENCH_COUNT=5
make bench-cpu   # benchmarks at GOMAXPROCS 1, 4 and 8
make test-all    # vet + race tests + benchmarks
```
