package main

import (
	"context"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// recordingObserver counts Observer calls and tracks peak concurrency.
type recordingObserver struct {
	submitted, started, finished atomic.Int64
	busy, peakBusy               atomic.Int64
	mu                           sync.Mutex
	perWorker                    map[int]int
	minTook                      time.Duration
}

func newRecordingObserver() *recordingObserver {
	return &recordingObserver{perWorker: map[int]int{}, minTook: time.Hour}
}

func (o *recordingObserver) TaskSubmitted(time.Duration) { o.submitted.Add(1) }

func (o *recordingObserver) TaskStarted(int) {
	o.started.Add(1)
	cur := o.busy.Add(1)
	for {
		old := o.peakBusy.Load()
		if cur <= old || o.peakBusy.CompareAndSwap(old, cur) {
			return
		}
	}
}

func (o *recordingObserver) TaskFinished(id int, took time.Duration) {
	o.busy.Add(-1)
	o.finished.Add(1)
	o.mu.Lock()
	o.perWorker[id]++
	o.minTook = min(o.minTook, took)
	o.mu.Unlock()
}

func TestObserverHook(t *testing.T) {
	const size, n = 4, 200
	obs := newRecordingObserver()
	p := New(size, 8, WithObserver(obs))
	for i := 0; i < n; i++ {
		if err := p.Submit(context.Background(), func(int) { time.Sleep(100 * time.Microsecond) }); err != nil {
			t.Fatal(err)
		}
	}
	p.Close()

	if got := obs.submitted.Load(); got != n {
		t.Errorf("TaskSubmitted called %d times, want %d", got, n)
	}
	if s, f := obs.started.Load(), obs.finished.Load(); s != n || f != n {
		t.Errorf("started=%d finished=%d, want %d", s, f, n)
	}
	if got := obs.busy.Load(); got != 0 {
		t.Errorf("busy after Close = %d, want 0", got)
	}
	if got := obs.peakBusy.Load(); got > size {
		t.Errorf("peak busy %d exceeds pool size %d", got, size)
	}
	for id := range obs.perWorker {
		if id < 0 || id >= size {
			t.Errorf("out-of-range worker ID %d", id)
		}
	}
	if obs.minTook < 100*time.Microsecond {
		t.Errorf("min task duration %v, want >= 100µs", obs.minTook)
	}
}

func TestObserverNotCalledOnFailedSubmit(t *testing.T) {
	obs := newRecordingObserver()
	p := New(1, 0, WithObserver(obs))
	p.Close()
	_ = p.Submit(context.Background(), func(int) {})
	if got := obs.submitted.Load(); got != 0 {
		t.Fatalf("TaskSubmitted called %d times for a rejected task", got)
	}
}

func TestPromObserver(t *testing.T) {
	const workers, n = 3, 300
	reg := prometheus.NewRegistry()
	obs, attach := NewPromObserver(reg, workers)
	p := New(workers, 16, WithObserver(obs))
	attach(p)
	for i := 0; i < n; i++ {
		if err := p.Submit(context.Background(), func(int) {}); err != nil {
			t.Fatal(err)
		}
	}
	p.Close()

	if got := testutil.ToFloat64(obs.submitted); got != n {
		t.Errorf("tasks_submitted_total = %v, want %d", got, n)
	}
	var sum float64
	for _, c := range obs.processed {
		sum += testutil.ToFloat64(c)
	}
	if sum != n {
		t.Errorf("sum of tasks_processed_total = %v, want %d", sum, n)
	}
	if got := testutil.ToFloat64(obs.busy); got != 0 {
		t.Errorf("busy_workers = %v, want 0", got)
	}

	want := `
# HELP threadpool_queue_capacity Capacity of the task queue.
# TYPE threadpool_queue_capacity gauge
threadpool_queue_capacity 16
# HELP threadpool_queue_depth Tasks waiting in the queue.
# TYPE threadpool_queue_depth gauge
threadpool_queue_depth 0
# HELP threadpool_workers Fixed number of pool workers.
# TYPE threadpool_workers gauge
threadpool_workers 3
`
	if err := testutil.GatherAndCompare(reg, strings.NewReader(want),
		"threadpool_queue_capacity", "threadpool_queue_depth", "threadpool_workers"); err != nil {
		t.Error(err)
	}
	for _, name := range []string{"threadpool_task_duration_seconds", "threadpool_submit_wait_seconds"} {
		if c, err := testutil.GatherAndCount(reg, name); err != nil || c != 1 {
			t.Errorf("%s: count=%d err=%v, want one histogram", name, c, err)
		}
	}
	if c, _ := testutil.GatherAndCount(reg, "threadpool_tasks_processed_total"); c != workers {
		t.Errorf("tasks_processed_total has %d series, want %d", c, workers)
	}
}

func TestProduceAtRate(t *testing.T) {
	p := New(4, 16)
	results := make(chan Result, 1024)
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	n, err := produceAtRate(ctx, p, 1000, 2, 0, results)
	p.Close()
	close(results)
	if err != nil {
		t.Fatal(err)
	}
	got := int64(len(results))
	if got != n {
		t.Fatalf("processed %d, submitted %d", got, n)
	}
	// ~200 expected at 1000/s for 200ms; allow a wide margin for slow CI.
	if n < 50 || n > 400 {
		t.Fatalf("submitted %d messages in 200ms at 1000/s", n)
	}
}

func TestProduceAtRateStopsOnClosedPool(t *testing.T) {
	p := New(1, 1)
	p.Close()
	results := make(chan Result, 10)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if _, err := produceAtRate(ctx, p, 1000, 2, 0, results); err == nil {
		t.Fatal("expected ErrPoolClosed")
	}
}
