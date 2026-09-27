// Command workerpool demonstrates a dynamically sized worker pool. A producer
// submits tasks in alternating bursts and lulls, and a controller grows and
// shrinks the pool to match the queue depth. On completion (or Ctrl-C) the
// pool is closed gracefully and all queued tasks are drained.
package main

import (
	"context"
	"flag"
	"log/slog"
	"math/rand/v2"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/controller"
	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

func main() {
	minWorkers := flag.Int("min", 1, "minimum number of workers")
	maxWorkers := flag.Int("max", 16, "maximum number of workers")
	queueSize := flag.Int("queue", 64, "task queue capacity")
	duration := flag.Duration("duration", 10*time.Second, "how long to produce tasks")
	interval := flag.Duration("interval", 250*time.Millisecond, "controller check interval")
	taskTime := flag.Duration("task", 50*time.Millisecond, "average task duration")
	flag.Parse()

	logger := slog.New(slog.NewTextHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	// ctx is cancelled on SIGINT/SIGTERM; the pool itself runs on a separate
	// context so a signal triggers a graceful drain instead of a hard stop.
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	pool, err := workerpool.New(context.Background(), workerpool.Config{
		Min: *minWorkers, Max: *maxWorkers, QueueSize: *queueSize,
	})
	if err != nil {
		logger.Error("creating pool", "err", err)
		os.Exit(1)
	}

	ctrl := &controller.Controller{
		Pool:      pool,
		Interval:  *interval,
		HighWater: *queueSize / 4,
		LowWater:  0,
		Step:      2,
		Logger:    logger,
	}
	ctrlCtx, ctrlCancel := context.WithCancel(ctx)
	var ctrlWG sync.WaitGroup
	ctrlWG.Add(1)
	go func() {
		defer ctrlWG.Done()
		ctrl.Run(ctrlCtx)
	}()

	var submitted, processed atomic.Int64
	produce(ctx, pool, *duration, *taskTime, &submitted, &processed)

	ctrlCancel()
	ctrlWG.Wait()

	logger.Info("closing pool, draining queue", "workers", pool.Size(), "queued", pool.QueueLen())
	pool.Close()
	logger.Info("done", "submitted", submitted.Load(), "processed", processed.Load())
}

// produce submits tasks until duration elapses or ctx is cancelled, switching
// between a burst phase (fast submissions) and a lull phase (slow submissions).
func produce(ctx context.Context, pool *workerpool.Pool, duration, taskTime time.Duration, submitted, processed *atomic.Int64) {
	ctx, cancel := context.WithTimeout(ctx, duration)
	defer cancel()

	const phase = 2 * time.Second
	start := time.Now()
	for ctx.Err() == nil {
		burst := (time.Since(start)/phase)%2 == 0
		gap := taskTime // lull: roughly one worker's worth of work
		if burst {
			gap = taskTime / 10
		}

		work := time.Duration(rand.Int64N(int64(2*taskTime) + 1))
		err := pool.Submit(ctx, func(context.Context) {
			time.Sleep(work) // simulate work
			processed.Add(1)
		})
		if err != nil {
			return
		}
		submitted.Add(1)

		select {
		case <-ctx.Done():
		case <-time.After(gap):
		}
	}
}
