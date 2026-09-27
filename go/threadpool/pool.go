package main

import (
	"context"
	"errors"
	"sync"
)

// ErrPoolClosed is returned by Submit once Close has been called.
var ErrPoolClosed = errors.New("threadpool: pool is closed")

// Task is a unit of work executed by one of the pool's workers. The worker ID
// (0..size-1) is passed in so tasks can report which worker ran them.
type Task func(workerID int)

// Pool is a static (fixed-size) worker pool. A fixed number of goroutines are
// started up front and consume tasks from a bounded queue. When the queue is
// full, Submit blocks, which gives producers natural backpressure.
type Pool struct {
	tasks chan Task
	wg    sync.WaitGroup

	// mu guards closed and protects tasks from being closed while a Submit
	// is sending on it (sending on a closed channel panics).
	mu     sync.RWMutex
	closed bool
}

// New starts a pool with `size` workers and a task queue of `queueSize`.
// size must be >= 1; queueSize may be 0 (unbuffered hand-off).
func New(size, queueSize int) *Pool {
	if size < 1 {
		panic("threadpool: size must be >= 1")
	}
	if queueSize < 0 {
		panic("threadpool: queueSize must be >= 0")
	}
	p := &Pool{tasks: make(chan Task, queueSize)}
	p.wg.Add(size)
	for i := 0; i < size; i++ {
		go p.worker(i)
	}
	return p
}

func (p *Pool) worker(id int) {
	defer p.wg.Done()
	for task := range p.tasks {
		task(id)
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
	select {
	case p.tasks <- task:
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
