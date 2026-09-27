package workerpool

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func newPool(t *testing.T, ctx context.Context, cfg Config) *Pool {
	t.Helper()
	p, err := New(ctx, cfg)
	if err != nil {
		t.Fatalf("New: %v", err)
	}
	return p
}

func eventually(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not met in time")
		}
		time.Sleep(time.Millisecond)
	}
}

func TestNewValidatesConfig(t *testing.T) {
	for _, cfg := range []Config{{Min: 0, Max: 1}, {Min: 2, Max: 1}, {Min: 1, Max: 1, QueueSize: -1}} {
		if _, err := New(context.Background(), cfg); err == nil {
			t.Errorf("New(%+v) succeeded, want error", cfg)
		}
	}
}

func TestGrowShrinkBounds(t *testing.T) {
	p := newPool(t, context.Background(), Config{Min: 2, Max: 5})
	defer p.Close()

	if got := p.Size(); got != 2 {
		t.Fatalf("initial size %d, want 2", got)
	}
	if err := p.Grow(3); err != nil {
		t.Fatalf("Grow(3): %v", err)
	}
	if err := p.Grow(1); !errors.Is(err, ErrBounds) {
		t.Fatalf("Grow past max: got %v, want ErrBounds", err)
	}
	if err := p.Shrink(3); err != nil {
		t.Fatalf("Shrink(3): %v", err)
	}
	if err := p.Shrink(1); !errors.Is(err, ErrBounds) {
		t.Fatalf("Shrink past min: got %v, want ErrBounds", err)
	}
	if got := p.Size(); got != 2 {
		t.Fatalf("size %d, want 2", got)
	}
	// stopped workers actually exit
	eventually(t, func() bool { return p.Running() == 2 })
}

func TestShrinkLimitsConcurrency(t *testing.T) {
	p := newPool(t, context.Background(), Config{Min: 1, Max: 8, QueueSize: 100})
	defer p.Close()
	if err := p.Grow(7); err != nil {
		t.Fatal(err)
	}
	if err := p.Shrink(6); err != nil {
		t.Fatal(err)
	}
	eventually(t, func() bool { return p.Running() == 2 })

	var active, peak atomic.Int64
	for i := 0; i < 50; i++ {
		err := p.Submit(context.Background(), func(context.Context) {
			n := active.Add(1)
			for {
				old := peak.Load()
				if n <= old || peak.CompareAndSwap(old, n) {
					break
				}
			}
			time.Sleep(time.Millisecond)
			active.Add(-1)
		})
		if err != nil {
			t.Fatal(err)
		}
	}
	p.Close()
	if got := peak.Load(); got > 2 {
		t.Fatalf("peak concurrency %d, want <= 2", got)
	}
}

func TestCloseDrainsQueue(t *testing.T) {
	p := newPool(t, context.Background(), Config{Min: 2, Max: 2, QueueSize: 100})
	var done atomic.Int64
	for i := 0; i < 100; i++ {
		if err := p.Submit(context.Background(), func(context.Context) { done.Add(1) }); err != nil {
			t.Fatal(err)
		}
	}
	p.Close()
	if got := done.Load(); got != 100 {
		t.Fatalf("processed %d tasks, want 100", got)
	}
	if err := p.Submit(context.Background(), func(context.Context) {}); !errors.Is(err, ErrClosed) {
		t.Fatalf("Submit after Close: got %v, want ErrClosed", err)
	}
	if err := p.Grow(1); !errors.Is(err, ErrClosed) {
		t.Fatalf("Grow after Close: got %v, want ErrClosed", err)
	}
	p.Close() // idempotent
}

func TestContextCancelStopsPool(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	p := newPool(t, ctx, Config{Min: 1, Max: 1})

	started := make(chan struct{})
	if err := p.Submit(context.Background(), func(ctx context.Context) {
		close(started)
		<-ctx.Done()
	}); err != nil {
		t.Fatal(err)
	}
	<-started
	cancel()

	// queue is unbuffered and the only worker is gone: Submit must not block
	if err := p.Submit(context.Background(), func(context.Context) {}); !errors.Is(err, context.Canceled) {
		t.Fatalf("Submit after cancel: got %v, want context.Canceled", err)
	}
	p.Close()
	if got := p.Running(); got != 0 {
		t.Fatalf("running %d after Close, want 0", got)
	}
}

// TestConcurrentUse exercises Submit, Grow, Shrink and Close concurrently; run
// with -race.
func TestConcurrentUse(t *testing.T) {
	p := newPool(t, context.Background(), Config{Min: 1, Max: 10, QueueSize: 4})
	var wg sync.WaitGroup
	var processed, submitted atomic.Int64

	for i := 0; i < 4; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				if err := p.Submit(context.Background(), func(context.Context) { processed.Add(1) }); err != nil {
					return
				}
				submitted.Add(1)
			}
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		for i := 0; i < 200; i++ {
			_ = p.Grow(1 + i%3)
			_ = p.Shrink(1 + i%2)
		}
	}()

	time.Sleep(50 * time.Millisecond)
	p.Close()
	wg.Wait()
	if s, n := submitted.Load(), processed.Load(); s != n {
		t.Fatalf("submitted %d but processed %d", s, n)
	}
}
