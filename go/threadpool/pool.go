package main

import (
	"context"
	"errors"
	"sync"
	"time"
)

// ErrPoolClosed is returned by Submit once Close has been called.
var ErrPoolClosed = errors.New("threadpool: pool is closed")

// Task is a unit of work executed by one of the pool's workers. The worker ID
// (0..size-1) is passed in so tasks can report which worker ran them.
type Task func(workerID int)

// Observer receives pool events, e.g. to export metrics. The pool has no
// dependency on any metrics library; see metrics.go for a Prometheus
// implementation. Methods are called from producer and worker goroutines
// concurrently, so implementations must be safe for concurrent use and fast.
type Observer interface {
	// TaskSubmitted is called after a task was queued; wait is how long
	// Submit blocked (e.g. on a full queue) before the task was accepted.
	TaskSubmitted(wait time.Duration)
	// TaskStarted is called by a worker right before it runs a task.
	TaskStarted(workerID int)
	// TaskFinished is called by a worker after a task returns.
	TaskFinished(workerID int, took time.Duration)
}

// Option configures a Pool.
type Option func(*Pool)

// WithObserver makes the pool report submit and task events to o.
func WithObserver(o Observer) Option {
	return func(p *Pool) { p.obs = o }
}

// Pool is a static (fixed-size) worker pool. A fixed number of goroutines are
// started up front and consume tasks from a bounded queue. When the queue is
// full, Submit blocks, which gives producers natural backpressure.
type Pool struct {
	tasks chan Task
	wg    sync.WaitGroup
	size  int
	obs   Observer // nil unless WithObserver is used

	// mu guards closed and protects tasks from being closed while a Submit
	// is sending on it (sending on a closed channel panics).
	mu     sync.RWMutex
	closed bool
}

// New starts a pool with `size` workers and a task queue of `queueSize`.
// size must be >= 1; queueSize may be 0 (unbuffered hand-off).
func New(size, queueSize int, opts ...Option) *Pool {
	if size < 1 {
		panic("threadpool: size must be >= 1")
	}
	if queueSize < 0 {
		panic("threadpool: queueSize must be >= 0")
	}
	p := &Pool{tasks: make(chan Task, queueSize), size: size}
	for _, opt := range opts {
		opt(p)
	}
	p.wg.Add(size)
	for i := 0; i < size; i++ {
		go p.worker(i)
	}
	return p
}

// Size returns the fixed number of workers.
func (p *Pool) Size() int { return p.size }

// QueueLen returns the number of tasks currently waiting in the queue.
func (p *Pool) QueueLen() int { return len(p.tasks) }

// QueueCap returns the capacity of the task queue.
func (p *Pool) QueueCap() int { return cap(p.tasks) }

func (p *Pool) worker(id int) {
	defer p.wg.Done()
	if p.obs == nil {
		for task := range p.tasks {
			task(id)
		}
		return
	}
	for task := range p.tasks {
		p.obs.TaskStarted(id)
		start := time.Now()
		task(id)
		p.obs.TaskFinished(id, time.Since(start))
	}
}

// Submit queues a task, blocking while the queue is full. It returns
// ErrPoolClosed if the pool has been closed, or ctx.Err() if ctx is done
// before the task could be queued.
func (p *Pool) Submit(ctx context.Context, task Task) error {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if p.closed {
		return ErrPoolClosed
	}
	if p.obs == nil {
		select {
		case p.tasks <- task:
			return nil
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	start := time.Now()
	select {
	case p.tasks <- task:
		p.obs.TaskSubmitted(time.Since(start))
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

// Close stops accepting new tasks, lets the workers drain everything already
// queued, and waits for them to exit. It is safe to call more than once.
func (p *Pool) Close() {
	p.mu.Lock()
	if !p.closed {
		p.closed = true
		close(p.tasks)
	}
	p.mu.Unlock()
	p.wg.Wait()
}
