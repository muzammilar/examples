// Command workerpool demonstrates a dynamically sized worker pool. A producer
// submits tasks in alternating bursts and lulls, and a controller grows and
// shrinks the pool to match the queue depth. On completion (or Ctrl-C) the
// pool is closed gracefully and all queued tasks are drained.
//
// With -metrics-addr set, Prometheus metrics are served on /metrics. With
// -duration 0 the producer runs until SIGINT/SIGTERM.
package main

import (
	"context"
	"errors"
	"flag"
	"log/slog"
	"math/rand/v2"
	"net"
	"net/http"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/controller"
	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/metrics"
	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

func main() {
	minWorkers := flag.Int("min", 1, "minimum number of workers")
	maxWorkers := flag.Int("max", 16, "maximum number of workers")
	queueSize := flag.Int("queue", 64, "task queue capacity")
	duration := flag.Duration("duration", 10*time.Second, "how long to produce tasks (0 = until SIGINT/SIGTERM)")
	interval := flag.Duration("interval", 250*time.Millisecond, "controller check interval")
	taskTime := flag.Duration("task", 50*time.Millisecond, "average task duration")
	phase := flag.Duration("phase", 2*time.Second, "length of each burst and each lull")
	metricsAddr := flag.String("metrics-addr", "", "serve Prometheus metrics on this address at /metrics, e.g. :8080 (empty = disabled)")
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

	// Metrics live on a private registry; they are only exposed when
	// -metrics-addr is set.
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	m, err := metrics.New(reg, pool)
	if err != nil {
		logger.Error("registering metrics", "err", err)
		os.Exit(1)
	}
	var srv *http.Server
	if *metricsAddr != "" {
		srv, _, err = serveMetrics(*metricsAddr, reg, logger)
		if err != nil {
			logger.Error("starting metrics server", "err", err)
			os.Exit(1)
		}
	}

	ctrl := &controller.Controller{
		Pool:      m.WrapScaler(pool),
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
	produce(ctx, pool, m, *duration, *phase, *taskTime, &submitted, &processed)

	ctrlCancel()
	ctrlWG.Wait()

	logger.Info("closing pool, draining queue", "workers", pool.Size(), "queued", pool.QueueLen())
	pool.Close()
	if srv != nil {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}
	logger.Info("done", "submitted", submitted.Load(), "processed", processed.Load())
}

// serveMetrics listens on addr and serves the metrics in reg on /metrics in
// the background. The listener is opened synchronously so address errors are
// returned to the caller, along with the bound address.
func serveMetrics(addr string, reg *prometheus.Registry, logger *slog.Logger) (*http.Server, string, error) {
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return nil, "", err
	}
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{Registry: reg}))
	srv := &http.Server{Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := srv.Serve(ln); err != nil && !errors.Is(err, http.ErrServerClosed) {
			logger.Error("metrics server", "err", err)
		}
	}()
	logger.Info("serving metrics", "addr", ln.Addr().String(), "path", "/metrics")
	return srv, ln.Addr().String(), nil
}

// produce submits tasks until duration elapses (never, if duration is 0) or
// ctx is cancelled, switching between a burst phase (fast submissions) and a
// lull phase (slow submissions) every phase.
func produce(ctx context.Context, pool *workerpool.Pool, m *metrics.Metrics, duration, phase, taskTime time.Duration, submitted, processed *atomic.Int64) {
	if duration > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, duration)
		defer cancel()
	}
	phase = max(phase, time.Millisecond)

	start := time.Now()
	for ctx.Err() == nil {
		burst := (time.Since(start)/phase)%2 == 0
		gap := taskTime // lull: roughly one worker's worth of work
		if burst {
			gap = taskTime / 10
		}

		work := time.Duration(rand.Int64N(int64(2*taskTime) + 1))
		err := pool.Submit(ctx, m.WrapTask(func(context.Context) {
			time.Sleep(work) // simulate work
			processed.Add(1)
		}))
		if err != nil {
			return
		}
		submitted.Add(1)
		m.Submitted()

		select {
		case <-ctx.Done():
		case <-time.After(gap):
		}
	}
}
