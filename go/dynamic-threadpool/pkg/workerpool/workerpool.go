// Package workerpool implements a goroutine pool whose size can be changed at
// runtime with Grow() and Shrink().
package workerpool

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/worker"
)

var (
	// ErrClosed is returned when using a pool after Close has been called.
	ErrClosed = errors.New("workerpool: pool is closed")
	// ErrBounds is returned when a resize would move the pool outside [Min, Max].
	ErrBounds = errors.New("workerpool: size out of bounds")
)

// Config configures a Pool.
type Config struct {
	Min       int // minimum number of workers (at least 1)
	Max       int // maximum number of workers (at least Min)
	QueueSize int // capacity of the task queue
}

// Pool is a dynamically sized pool of workers sharing a single task queue.
type Pool struct {
	ctx context.Context
	cfg Config

	// submitMu guards closed and the jobs channel against a concurrent
	// Submit and Close (sending on a closed channel panics).
	submitMu sync.RWMutex
	closed   bool
	jobs     chan worker.Task

	// mu guards the set of active workers.
	mu      sync.Mutex
	stopped bool             // set by Close; no more resizing
	workers []*worker.Worker // ordered by ID, ascending
	nextID  int

	wg      sync.WaitGroup
	running atomic.Int64 // goroutines that have not exited yet
}

// New creates a pool with cfg.Min workers. Cancelling ctx stops all workers
// immediately without draining the queue; use Close for a graceful shutdown.
func New(ctx context.Context, cfg Config) (*Pool, error) {
	if cfg.Min < 1 || cfg.Max < cfg.Min || cfg.QueueSize < 0 {
		return nil, fmt.Errorf("workerpool: invalid config %+v", cfg)
	}
	p := &Pool{
		ctx:  ctx,
		cfg:  cfg,
		jobs: make(chan worker.Task, cfg.QueueSize),
	}
	if err := p.Grow(cfg.Min); err != nil {
		return nil, err
	}
	return p, nil
}

// Submit enqueues a task. It blocks while the queue is full, and returns early
// if ctx or the pool's context is cancelled, or if the pool is closed.
func (p *Pool) Submit(ctx context.Context, task worker.Task) error {
	p.submitMu.RLock()
	defer p.submitMu.RUnlock()
	if p.closed {
		return ErrClosed
	}
	select {
	case p.jobs <- task:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-p.ctx.Done():
		return p.ctx.Err()
	}
}

// Grow adds n workers to the pool.
func (p *Pool) Grow(n int) error {
	if n < 0 {
		return fmt.Errorf("workerpool: grow by %d: %w", n, ErrBounds)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopped {
		return ErrClosed
	}
	if len(p.workers)+n > p.cfg.Max {
		return fmt.Errorf("workerpool: grow %d+%d > max %d: %w", len(p.workers), n, p.cfg.Max, ErrBounds)
	}
	for i := 0; i < n; i++ {
		p.nextID++
		w := worker.New(p.nextID)
		p.workers = append(p.workers, w)
		p.wg.Add(1)
		p.running.Add(1)
		go func() {
			defer p.wg.Done()
			defer p.running.Add(-1)
			w.Run(p.ctx, p.jobs)
		}()
	}
	return nil
}

// Shrink signals the n workers with the highest IDs to stop. A stopped worker
// finishes the task it is currently running (and, if a task arrives at the same
// instant as the stop signal, at most that one task) before exiting.
func (p *Pool) Shrink(n int) error {
	if n < 0 {
		return fmt.Errorf("workerpool: shrink by %d: %w", n, ErrBounds)
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.stopped {
		return ErrClosed
	}
	if len(p.workers)-n < p.cfg.Min {
		return fmt.Errorf("workerpool: shrink %d-%d < min %d: %w", len(p.workers), n, p.cfg.Min, ErrBounds)
	}
	keep := len(p.workers) - n
	for _, w := range p.workers[keep:] {
		w.Stop()
	}
	clear(p.workers[keep:]) // drop references to stopped workers
	p.workers = p.workers[:keep]
	return nil
}

// Size returns the number of active (not stopped) workers.
func (p *Pool) Size() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.workers)
}

// Running returns the number of worker goroutines that have not exited yet.
// It can briefly exceed Size after Shrink while stopped workers finish up.
func (p *Pool) Running() int {
	return int(p.running.Load())
}

// QueueLen returns the number of tasks waiting in the queue.
func (p *Pool) QueueLen() int {
	return len(p.jobs)
}

// Bounds returns the configured minimum and maximum pool size.
func (p *Pool) Bounds() (minSize, maxSize int) {
	return p.cfg.Min, p.cfg.Max
}

// Close stops accepting new tasks, lets the workers drain the queue, and waits
// for all workers (including ones removed by Shrink) to exit. It is safe to
// call Close more than once.
func (p *Pool) Close() {
	p.submitMu.Lock()
	if !p.closed {
		p.closed = true
		close(p.jobs)
	}
	p.submitMu.Unlock()

	// No Grow can call wg.Add after this point, so wg.Wait below is safe.
	p.mu.Lock()
	p.stopped = true
	p.workers = nil // workers exit on their own once the queue is drained
	p.mu.Unlock()

	p.wg.Wait()
}
