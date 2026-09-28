package main

import (
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// Metric names exported by the demo (all prefixed with "threadpool_").
const metricsNamespace = "threadpool"

// durationBuckets spans 1µs .. ~2s in 22 doubling buckets: submit waits are
// often sub-microsecond, tasks take micro- to milliseconds, and waits can
// grow to seconds when the queue is saturated.
var durationBuckets = prometheus.ExponentialBuckets(1e-6, 2, 22)

// PromObserver is a Pool Observer that records Prometheus metrics. It is only
// used by the demo; the Pool itself does not depend on Prometheus.
type PromObserver struct {
	submitted  prometheus.Counter
	processed  []prometheus.Counter // indexed by worker ID
	busy       prometheus.Gauge
	taskDur    prometheus.Histogram
	submitWait prometheus.Histogram
}

// NewPromObserver creates the pool metrics for a pool of `workers` workers
// and registers them on reg (use a private registry, not the global one).
// It returns the observer to pass to WithObserver and a function that
// registers the gauges that read the pool's state once the pool exists.
func NewPromObserver(reg prometheus.Registerer, workers int) (*PromObserver, func(*Pool)) {
	processed := prometheus.NewCounterVec(prometheus.CounterOpts{
		Namespace: metricsNamespace,
		Name:      "tasks_processed_total",
		Help:      "Tasks completed, by worker.",
	}, []string{"worker"})
	o := &PromObserver{
		submitted: prometheus.NewCounter(prometheus.CounterOpts{
			Namespace: metricsNamespace,
			Name:      "tasks_submitted_total",
			Help:      "Tasks accepted by Submit.",
		}),
		busy: prometheus.NewGauge(prometheus.GaugeOpts{
			Namespace: metricsNamespace,
			Name:      "busy_workers",
			Help:      "Workers currently running a task.",
		}),
		taskDur: prometheus.NewHistogram(prometheus.HistogramOpts{
			Namespace: metricsNamespace,
			Name:      "task_duration_seconds",
			Help:      "Time a worker spent running a task.",
			Buckets:   durationBuckets,
		}),
		submitWait: prometheus.NewHistogram(prometheus.HistogramOpts{
			Namespace: metricsNamespace,
			Name:      "submit_wait_seconds",
			Help:      "Time Submit blocked before the task was queued.",
			Buckets:   durationBuckets,
		}),
	}
	// Pre-create one series per worker so idle workers show up as 0.
	o.processed = make([]prometheus.Counter, workers)
	for i := range o.processed {
		o.processed[i] = processed.WithLabelValues(strconv.Itoa(i))
	}
	reg.MustRegister(o.submitted, processed, o.busy, o.taskDur, o.submitWait)

	attach := func(p *Pool) {
		reg.MustRegister(
			prometheus.NewGaugeFunc(prometheus.GaugeOpts{
				Namespace: metricsNamespace,
				Name:      "queue_depth",
				Help:      "Tasks waiting in the queue.",
			}, func() float64 { return float64(p.QueueLen()) }),
			prometheus.NewGaugeFunc(prometheus.GaugeOpts{
				Namespace: metricsNamespace,
				Name:      "queue_capacity",
				Help:      "Capacity of the task queue.",
			}, func() float64 { return float64(p.QueueCap()) }),
			prometheus.NewGaugeFunc(prometheus.GaugeOpts{
				Namespace: metricsNamespace,
				Name:      "workers",
				Help:      "Fixed number of pool workers.",
			}, func() float64 { return float64(p.Size()) }),
		)
	}
	return o, attach
}

// TaskSubmitted implements Observer.
func (o *PromObserver) TaskSubmitted(wait time.Duration) {
	o.submitted.Inc()
	o.submitWait.Observe(wait.Seconds())
}

// TaskStarted implements Observer.
func (o *PromObserver) TaskStarted(int) { o.busy.Inc() }

// TaskFinished implements Observer.
func (o *PromObserver) TaskFinished(workerID int, took time.Duration) {
	o.busy.Dec()
	o.processed[workerID].Inc()
	o.taskDur.Observe(took.Seconds())
}
