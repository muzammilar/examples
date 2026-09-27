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
* `pkg/controller`: A single **controller** goroutine that periodically monitors the queue depth and resizes the pool. The logic is intentionally primitive: grow by `Step` when more than `HighWater` tasks are queued, shrink by `Step` when at most `LowWater` tasks are queued.

The demo in `cmd/workerpool` produces tasks in alternating bursts and lulls (2 seconds each), so the pool can be seen growing and shrinking in the logs. It shuts down gracefully (draining the queue) when `-duration` elapses or on `Ctrl-C`.

```sh
# run locally
go run ./cmd/workerpool -min 1 -max 16 -queue 64 -duration 10s
go run ./cmd/workerpool -h

# run the tests (with the race detector)
go test -race ./...

# run the benchmarks (Submit throughput, Grow/Shrink cost, Submit while
# the controller is scaling, controller decision cost)
go test -bench=. -benchmem ./...
```

```sh
# build and run the containers
docker-compose up --build
# delete the containers and their images
docker-compose down --rmi all --volumes
# Remove volumes
docker-compose rm --force --stop -v
```
