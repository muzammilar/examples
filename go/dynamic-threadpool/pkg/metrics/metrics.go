// Package metrics exposes Prometheus metrics for a worker pool without
// touching the pool itself. Nothing is registered globally: callers pass the
// prometheus.Registerer to use (for example a fresh prometheus.NewRegistry()).
//
//   - Pool state (size, running workers, bounds, queue length) is read on every
//     scrape by a custom Collector, so it is always current and costs nothing
//     between scrapes.
//   - Scale events are counted by wrapping the controller's Scaler.
//   - Task counts and durations are recorded by wrapping each task.
package metrics

import (
	"context"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/controller"
	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/worker"
)

// Namespace is the prefix of every metric name.
const Namespace = "workerpool"

// Scale event directions (values of the "direction" label).
const (
	DirectionGrow   = "grow"
	DirectionShrink = "shrink"
)

// PoolStats is the read-only subset of the pool API the collector needs.
type PoolStats interface {
	Size() int
	Running() int
	QueueLen() int
	Bounds() (minSize, maxSize int)
}

// Metrics holds the pool metrics. Create it with New.
type Metrics struct {
	scaleEvents    *prometheus.CounterVec
	tasksSubmitted prometheus.Counter
	tasksProcessed prometheus.Counter
	taskDuration   prometheus.Histogram
}

// New creates the metrics and registers them, plus a collector for pool, with
// reg.
func New(reg prometheus.Registerer, pool PoolStats) (*Metrics, error) {
	m := &Metrics{
		scaleEvents: prometheus.NewCounterVec(prometheus.CounterOpts{
			Namespace: Namespace,
			Name:      "scale_events_total",
			Help:      "Number of successful pool resizes, by direction.",
		}, []string{"direction"}),
		tasksSubmitted: prometheus.NewCounter(prometheus.CounterOpts{
			Namespace: Namespace,
			Name:      "tasks_submitted_total",
			Help:      "Number of tasks successfully submitted to the queue.",
		}),
		tasksProcessed: prometheus.NewCounter(prometheus.CounterOpts{
			Namespace: Namespace,
			Name:      "tasks_processed_total",
			Help:      "Number of tasks that finished running.",
		}),
		taskDuration: prometheus.NewHistogram(prometheus.HistogramOpts{
			Namespace: Namespace,
			Name:      "task_duration_seconds",
			Help:      "Time spent running a task.",
			Buckets:   prometheus.ExponentialBuckets(0.005, 2, 10), // 5ms .. ~2.56s
		}),
	}
	// Initialise both directions so the series exist before the first resize.
	m.scaleEvents.WithLabelValues(DirectionGrow)
	m.scaleEvents.WithLabelValues(DirectionShrink)

	for _, c := range []prometheus.Collector{
		m.scaleEvents, m.tasksSubmitted, m.tasksProcessed, m.taskDuration,
		NewPoolCollector(pool),
	} {
		if err := reg.Register(c); err != nil {
			return nil, err
		}
	}
	return m, nil
}

// Submitted records a task that was accepted by the pool.
func (m *Metrics) Submitted() { m.tasksSubmitted.Inc() }

// WrapTask returns a task that runs t and records its duration and completion.
func (m *Metrics) WrapTask(t worker.Task) worker.Task {
	return func(ctx context.Context) {
		start := time.Now()
		defer func() {
			m.taskDuration.Observe(time.Since(start).Seconds())
			m.tasksProcessed.Inc()
		}()
		t(ctx)
	}
}

// WrapScaler returns a controller.Scaler that forwards to s and counts every
// successful non-empty Grow and Shrink.
func (m *Metrics) WrapScaler(s controller.Scaler) controller.Scaler {
	return &scaler{Scaler: s, m: m}
}

type scaler struct {
	controller.Scaler
	m *Metrics
}

func (s *scaler) Grow(n int) error {
	err := s.Scaler.Grow(n)
	if err == nil && n > 0 {
		s.m.scaleEvents.WithLabelValues(DirectionGrow).Inc()
	}
	return err
}

func (s *scaler) Shrink(n int) error {
	err := s.Scaler.Shrink(n)
	if err == nil && n > 0 {
		s.m.scaleEvents.WithLabelValues(DirectionShrink).Inc()
	}
	return err
}

// PoolCollector is a prometheus.Collector that reads the pool state on scrape.
type PoolCollector struct {
	pool                                   PoolStats
	size, running, minSize, maxSize, queue *prometheus.Desc
}

// NewPoolCollector returns a collector for pool's gauges.
func NewPoolCollector(pool PoolStats) *PoolCollector {
	desc := func(name, help string) *prometheus.Desc {
		return prometheus.NewDesc(prometheus.BuildFQName(Namespace, "", name), help, nil, nil)
	}
	return &PoolCollector{
		pool:    pool,
		size:    desc("size", "Target number of workers (active, not stopped)."),
		running: desc("running_workers", "Worker goroutines that have not exited yet."),
		minSize: desc("min_workers", "Configured minimum pool size."),
		maxSize: desc("max_workers", "Configured maximum pool size."),
		queue:   desc("queue_length", "Tasks waiting in the queue."),
	}
}

// Describe implements prometheus.Collector.
func (c *PoolCollector) Describe(ch chan<- *prometheus.Desc) {
	ch <- c.size
	ch <- c.running
	ch <- c.minSize
	ch <- c.maxSize
	ch <- c.queue
}

// Collect implements prometheus.Collector.
func (c *PoolCollector) Collect(ch chan<- prometheus.Metric) {
	minSize, maxSize := c.pool.Bounds()
	gauge := func(d *prometheus.Desc, v int) {
		ch <- prometheus.MustNewConstMetric(d, prometheus.GaugeValue, float64(v))
	}
	gauge(c.size, c.pool.Size())
	gauge(c.running, c.pool.Running())
	gauge(c.minSize, minSize)
	gauge(c.maxSize, maxSize)
	gauge(c.queue, c.pool.QueueLen())
}
