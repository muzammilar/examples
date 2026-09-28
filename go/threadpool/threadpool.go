// Command threadpool demonstrates a static (fixed-size) worker pool.
//
// Several producer goroutines generate messages and submit them to a pool
// with a fixed number of workers. Each worker "processes" a message and
// sends a result on a results channel, which main aggregates.
//
// By default it processes -messages messages as fast as possible and exits.
// With -rate N it runs continuously, producing N messages/sec for -duration
// (0 = until SIGINT/SIGTERM), then shuts down gracefully. -metrics-addr
// serves Prometheus metrics on /metrics.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"hash/fnv"
	"log"
	"math/rand/v2"
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
)

// Message is the unit of data flowing through the example.
type Message struct {
	ID      int
	Payload string
}

// Result is what a worker produces for a Message.
type Result struct {
	MessageID int
	WorkerID  int
	Checksum  uint32
}

// process is the (cheap, CPU-bound) work done for each message.
func process(msg *Message) uint32 {
	h := fnv.New32a()
	h.Write([]byte(msg.Payload))
	return h.Sum32()
}

// newTask builds the task for message id. If work > 0 the task also sleeps
// for a random duration in [work/2, 3*work/2) to simulate slower I/O-bound
// work (used by the continuous mode so the pool metrics are interesting).
func newTask(id int, work time.Duration, results chan<- Result) Task {
	msg := &Message{ID: id, Payload: fmt.Sprintf("message-%d", id)}
	return func(workerID int) {
		if work > 0 {
			time.Sleep(work/2 + rand.N(work))
		}
		results <- Result{MessageID: msg.ID, WorkerID: workerID, Checksum: process(msg)}
	}
}

// produce splits message IDs [0, count) across numProducers goroutines and
// submits one task per message to the pool. It returns when all messages
// have been submitted (or submission failed).
func produce(ctx context.Context, pool *Pool, count, numProducers int, results chan<- Result) error {
	var wg sync.WaitGroup
	errs := make(chan error, numProducers)
	for p := 0; p < numProducers; p++ {
		wg.Add(1)
		go func(producerID int) {
			defer wg.Done()
			for id := producerID; id < count; id += numProducers {
				if err := pool.Submit(ctx, newTask(id, 0, results)); err != nil {
					errs <- fmt.Errorf("producer %d: %w", producerID, err)
					return
				}
			}
		}(p)
	}
	wg.Wait()
	close(errs)
	return <-errs // nil if no producer failed
}

// produceAtRate submits about `rate` messages per second, spread across
// numProducers goroutines, until ctx is done. It returns the number of
// messages submitted. Cancellation of ctx is a normal stop, not an error.
func produceAtRate(ctx context.Context, pool *Pool, rate float64, numProducers int, work time.Duration, results chan<- Result) (int64, error) {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	// The pacer releases message IDs on a schedule; producers block on the
	// pool when it is saturated, and the pacer catches up afterwards.
	ids := make(chan int, numProducers)
	go func() {
		defer close(ids)
		start := time.Now()
		tick := time.NewTicker(10 * time.Millisecond)
		defer tick.Stop()
		next := 0
		for {
			select {
			case <-ctx.Done():
				return
			case now := <-tick.C:
				for due := int(rate * now.Sub(start).Seconds()); next < due; next++ {
					select {
					case ids <- next:
					case <-ctx.Done():
						return
					}
				}
			}
		}
	}()

	var submitted atomic.Int64
	var wg sync.WaitGroup
	errs := make(chan error, numProducers)
	for p := 0; p < numProducers; p++ {
		wg.Add(1)
		go func(producerID int) {
			defer wg.Done()
			for id := range ids {
				if err := pool.Submit(ctx, newTask(id, work, results)); err != nil {
					if ctx.Err() == nil {
						errs <- fmt.Errorf("producer %d: %w", producerID, err)
						cancel()
					}
					return
				}
				submitted.Add(1)
			}
		}(p)
	}
	wg.Wait()
	close(errs)
	return submitted.Load(), <-errs
}

// serveMetrics starts an HTTP server exposing reg on /metrics.
func serveMetrics(addr string, reg *prometheus.Registry) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{Registry: reg}))
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("metrics server: %v", err)
		}
	}()
	log.Printf("serving metrics on %s/metrics", addr)
	return srv
}

func main() {
	workers := flag.Int("workers", 8, "number of fixed pool workers")
	queue := flag.Int("queue", 64, "size of the pool's task queue")
	producers := flag.Int("producers", 4, "number of producer goroutines")
	messages := flag.Int("messages", 100000, "number of messages to process (one-shot mode)")
	rate := flag.Float64("rate", 0, "messages per second; > 0 enables continuous mode (ignores -messages)")
	duration := flag.Duration("duration", 0, "how long continuous mode runs; 0 = until SIGINT/SIGTERM")
	work := flag.Duration("task-time", 0, "simulated extra work per task (random in [t/2, 3t/2)); 0 = checksum only")
	metricsAddr := flag.String("metrics-addr", "", "serve Prometheus metrics on this address (e.g. :8080); empty = disabled")
	flag.Parse()

	if *workers < 1 || *producers < 1 || *queue < 0 || *messages < 0 || *rate < 0 || *duration < 0 || *work < 0 {
		log.Fatal("workers and producers must be >= 1; queue, messages, rate, duration and task-time must be >= 0")
	}
	if *duration > 0 && *rate == 0 {
		log.Fatal("-duration requires -rate > 0")
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	var opts []Option
	var attachMetrics func(*Pool)
	var srv *http.Server
	if *metricsAddr != "" {
		reg := prometheus.NewRegistry()
		reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
		obs, attach := NewPromObserver(reg, *workers)
		opts = append(opts, WithObserver(obs))
		attachMetrics = attach
		srv = serveMetrics(*metricsAddr, reg)
	}

	start := time.Now()
	pool := New(*workers, *queue, opts...)
	if attachMetrics != nil {
		attachMetrics(pool)
	}
	results := make(chan Result, *queue)

	// Aggregate results concurrently so workers never block for long.
	perWorker := make([]int, *workers)
	var xor uint32
	var processed atomic.Int64
	done := make(chan struct{})
	go func() {
		defer close(done)
		for r := range results {
			perWorker[r.WorkerID]++
			xor ^= r.Checksum
			processed.Add(1)
		}
	}()

	target := int64(*messages)
	if *rate > 0 {
		runCtx := ctx
		if *duration > 0 {
			var cancel context.CancelFunc
			runCtx, cancel = context.WithTimeout(ctx, *duration)
			defer cancel()
		}
		log.Printf("continuous mode: %g msgs/sec with %d workers (duration %v, 0 = until signal)", *rate, *workers, *duration)
		progressDone := make(chan struct{})
		go func() { // periodic progress so `docker compose logs` shows life
			tick := time.NewTicker(10 * time.Second)
			defer tick.Stop()
			for {
				select {
				case <-progressDone:
					return
				case <-tick.C:
					log.Printf("processed %d messages, queue depth %d", processed.Load(), pool.QueueLen())
				}
			}
		}()
		n, err := produceAtRate(runCtx, pool, *rate, *producers, *work, results)
		close(progressDone)
		if err != nil {
			log.Printf("submission stopped early: %v", err)
		}
		log.Printf("stopping: draining %d queued tasks", pool.QueueLen())
		target = n
	} else if err := produce(ctx, pool, *messages, *producers, results); err != nil {
		log.Printf("submission stopped early: %v", err)
	}
	pool.Close()   // drain queued tasks and wait for workers to exit
	close(results) // safe: no worker can send any more
	<-done

	total := 0
	for id, n := range perWorker {
		fmt.Printf("worker %2d processed %d messages\n", id, n)
		total += n
	}
	fmt.Printf("processed %d/%d messages with %d workers in %s (checksum xor %08x)\n",
		total, target, *workers, time.Since(start).Round(time.Millisecond), xor)

	if srv != nil {
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdownCtx)
	}
}
