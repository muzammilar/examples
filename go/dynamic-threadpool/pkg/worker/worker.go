// Package worker implements a single pool worker. A worker pulls tasks from a
// shared queue and runs them until it is told to stop, the queue is closed
// (graceful drain), or its context is cancelled (hard stop).
package worker

import "context"

// Task is a unit of work executed by a worker. The context passed to the task
// is the pool's context, so long-running tasks can observe shutdown.
type Task func(ctx context.Context)

// Worker is a single goroutine in the pool with its own dedicated quit
// channel, which allows the pool to remove a specific worker deterministically.
type Worker struct {
	ID   int
	quit chan struct{}
}

// New creates a worker with the given ID.
func New(id int) *Worker {
	return &Worker{ID: id, quit: make(chan struct{})}
}

// Stop signals the worker to exit after finishing its current task (if any).
// It must be called at most once.
func (w *Worker) Stop() {
	close(w.quit)
}

// Run executes tasks from jobs until one of the following happens:
//   - Stop is called: the worker finishes its current task and exits.
//   - jobs is closed: the worker drains the remaining tasks and exits.
//   - ctx is cancelled: the worker exits without draining.
//
// Run blocks, so it is normally called in its own goroutine.
func (w *Worker) Run(ctx context.Context, jobs <-chan Task) {
	for {
		// prioritize stop signals so a stopped worker does not keep picking up work
		select {
		case <-ctx.Done():
			return
		case <-w.quit:
			return
		default:
		}

		select {
		case <-ctx.Done():
			return
		case <-w.quit:
			return
		case task, ok := <-jobs:
			if !ok {
				return // queue closed and drained
			}
			task(ctx)
		}
	}
}
