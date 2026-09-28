package worker

import (
	"context"
	"testing"
	"time"
)

func runAsync(ctx context.Context, w *Worker, jobs <-chan Task) <-chan struct{} {
	done := make(chan struct{})
	go func() {
		defer close(done)
		w.Run(ctx, jobs)
	}()
	return done
}

func waitDone(t *testing.T, done <-chan struct{}) {
	t.Helper()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("worker did not exit")
	}
}

func TestRunDrainsClosedQueue(t *testing.T) {
	jobs := make(chan Task, 10)
	var n int
	for i := 0; i < 10; i++ {
		jobs <- func(context.Context) { n++ }
	}
	close(jobs)
	waitDone(t, runAsync(context.Background(), New(1), jobs))
	if n != 10 {
		t.Fatalf("ran %d tasks, want 10", n)
	}
}

func TestStopFinishesCurrentTask(t *testing.T) {
	jobs := make(chan Task)
	w := New(1)
	done := runAsync(context.Background(), w, jobs)

	started, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	jobs <- func(context.Context) {
		close(started)
		<-release
		close(finished)
	}
	<-started
	w.Stop()
	close(release)
	waitDone(t, done)
	select {
	case <-finished:
	default:
		t.Fatal("in-flight task was not completed")
	}
}

func TestContextCancelStopsWorker(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	done := runAsync(ctx, New(1), make(chan Task))
	cancel()
	waitDone(t, done)
}
