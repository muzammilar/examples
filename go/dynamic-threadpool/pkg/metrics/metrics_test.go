package metrics

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

// fakePool implements PoolStats and controller.Scaler.
type fakePool struct {
	size, running, queued, min, max int
	failGrow                        bool
}

func (f *fakePool) Size() int                      { return f.size }
func (f *fakePool) Running() int                   { return f.running }
func (f *fakePool) QueueLen() int                  { return f.queued }
func (f *fakePool) Bounds() (minSize, maxSize int) { return f.min, f.max }
func (f *fakePool) Grow(n int) error {
	if f.failGrow {
		return errors.New("grow failed")
	}
	f.size += n
	return nil
}
func (f *fakePool) Shrink(n int) error { f.size -= n; return nil }

func TestPoolCollector(t *testing.T) {
	f := &fakePool{size: 3, running: 4, queued: 7, min: 1, max: 8}
	c := NewPoolCollector(f)

	expected := `
# HELP workerpool_max_workers Configured maximum pool size.
# TYPE workerpool_max_workers gauge
workerpool_max_workers 8
# HELP workerpool_min_workers Configured minimum pool size.
# TYPE workerpool_min_workers gauge
workerpool_min_workers 1
# HELP workerpool_queue_length Tasks waiting in the queue.
# TYPE workerpool_queue_length gauge
workerpool_queue_length 7
# HELP workerpool_running_workers Worker goroutines that have not exited yet.
# TYPE workerpool_running_workers gauge
workerpool_running_workers 4
# HELP workerpool_size Target number of workers (active, not stopped).
# TYPE workerpool_size gauge
workerpool_size 3
`
	if err := testutil.CollectAndCompare(c, strings.NewReader(expected)); err != nil {
		t.Fatal(err)
	}

	// values are read on every collection
	f.size, f.queued = 5, 0
	if err := testutil.CollectAndCompare(c, strings.NewReader(`
# HELP workerpool_size Target number of workers (active, not stopped).
# TYPE workerpool_size gauge
workerpool_size 5
`), "workerpool_size"); err != nil {
		t.Fatal(err)
	}
}

func TestScaleEvents(t *testing.T) {
	reg := prometheus.NewPedanticRegistry()
	f := &fakePool{size: 2, min: 1, max: 8}
	m, err := New(reg, f)
	if err != nil {
		t.Fatal(err)
	}
	s := m.WrapScaler(f)

	// both directions exist before any resize
	if got := testutil.ToFloat64(m.scaleEvents.WithLabelValues(DirectionGrow)); got != 0 {
		t.Fatalf("grow before = %v, want 0", got)
	}

	_ = s.Grow(2)
	_ = s.Grow(0) // no-op, not an event
	_ = s.Shrink(1)
	f.failGrow = true
	_ = s.Grow(1) // failed, not an event

	if got := testutil.ToFloat64(m.scaleEvents.WithLabelValues(DirectionGrow)); got != 1 {
		t.Errorf("grow events = %v, want 1", got)
	}
	if got := testutil.ToFloat64(m.scaleEvents.WithLabelValues(DirectionShrink)); got != 1 {
		t.Errorf("shrink events = %v, want 1", got)
	}
	if s.Size() != 3 {
		t.Errorf("Size through wrapper = %d, want 3", s.Size())
	}
}

func TestTasksWithRealPool(t *testing.T) {
	pool, err := workerpool.New(context.Background(), workerpool.Config{Min: 2, Max: 4, QueueSize: 8})
	if err != nil {
		t.Fatal(err)
	}
	reg := prometheus.NewPedanticRegistry()
	m, err := New(reg, pool)
	if err != nil {
		t.Fatal(err)
	}

	const n = 10
	for range n {
		if err := pool.Submit(context.Background(), m.WrapTask(func(context.Context) { time.Sleep(time.Millisecond) })); err != nil {
			t.Fatal(err)
		}
		m.Submitted()
	}
	pool.Close()

	if got := testutil.ToFloat64(m.tasksSubmitted); got != n {
		t.Errorf("submitted = %v, want %d", got, n)
	}
	if got := testutil.ToFloat64(m.tasksProcessed); got != n {
		t.Errorf("processed = %v, want %d", got, n)
	}
	if got := testutil.CollectAndCount(m.taskDuration); got != 1 {
		t.Errorf("histogram series = %d, want 1", got)
	}

	// every advertised metric name is gathered (and the pedantic registry is happy)
	mfs, err := reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	got := map[string]bool{}
	for _, mf := range mfs {
		got[mf.GetName()] = true
	}
	for _, name := range []string{
		"workerpool_size", "workerpool_running_workers", "workerpool_min_workers",
		"workerpool_max_workers", "workerpool_queue_length", "workerpool_scale_events_total",
		"workerpool_tasks_submitted_total", "workerpool_tasks_processed_total",
		"workerpool_task_duration_seconds",
	} {
		if !got[name] {
			t.Errorf("metric %s not gathered", name)
		}
	}
}

func TestNewDuplicateRegistration(t *testing.T) {
	reg := prometheus.NewRegistry()
	f := &fakePool{min: 1, max: 1}
	if _, err := New(reg, f); err != nil {
		t.Fatal(err)
	}
	if _, err := New(reg, f); err == nil {
		t.Fatal("expected an error registering twice on the same registry")
	}
}
