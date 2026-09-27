package main

import (
	"context"
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestPoolRunsAllTasks(t *testing.T) {
	const n = 10000
	p := New(8, 16)
	var count atomic.Int64
	for i := 0; i < n; i++ {
		if err := p.Submit(context.Background(), func(int) { count.Add(1) }); err != nil {
			t.Fatalf("submit %d: %v", i, err)
		}
	}
	p.Close()
	if got := count.Load(); got != n {
		t.Fatalf("ran %d tasks, want %d", got, n)
	}
}

func TestPoolConcurrencyIsBounded(t *testing.T) {
	const size = 4
	p := New(size, 0)
	var running, peak atomic.Int64
	var wg sync.WaitGroup
	for i := 0; i < 50; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = p.Submit(context.Background(), func(int) {
				cur := running.Add(1)
				for {
					old := peak.Load()
					if cur <= old || peak.CompareAndSwap(old, cur) {
						break
					}
				}
				time.Sleep(time.Millisecond)
				running.Add(-1)
			})
		}()
	}
	wg.Wait()
	p.Close()
	if got := peak.Load(); got > size {
		t.Fatalf("peak concurrency %d exceeds pool size %d", got, size)
	}
}

func TestWorkerIDsInRange(t *testing.T) {
	const size = 3
	p := New(size, 8)
	var bad atomic.Bool
	for i := 0; i < 1000; i++ {
		_ = p.Submit(context.Background(), func(id int) {
			if id < 0 || id >= size {
				bad.Store(true)
			}
		})
	}
	p.Close()
	if bad.Load() {
		t.Fatal("task received out-of-range worker ID")
	}
}

func TestSubmitAfterClose(t *testing.T) {
	p := New(2, 2)
	p.Close()
	p.Close() // idempotent
	if err := p.Submit(context.Background(), func(int) {}); !errors.Is(err, ErrPoolClosed) {
		t.Fatalf("got %v, want ErrPoolClosed", err)
	}
}

func TestSubmitRespectsContext(t *testing.T) {
	p := New(1, 0)
	release := make(chan struct{})
	started := make(chan struct{})
	// Occupy the only worker so the next unbuffered Submit must block.
	if err := p.Submit(context.Background(), func(int) { close(started); <-release }); err != nil {
		t.Fatal(err)
	}
	<-started
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := p.Submit(ctx, func(int) {}); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("got %v, want DeadlineExceeded", err)
	}
	close(release)
	p.Close()
}

func TestProduceEndToEnd(t *testing.T) {
	const workers, count = 5, 2000
	p := New(workers, 10)
	results := make(chan Result, count)
	if err := produce(context.Background(), p, count, 3, results); err != nil {
		t.Fatal(err)
	}
	p.Close()
	close(results)
	seen := make(map[int]bool, count)
	for r := range results {
		if seen[r.MessageID] {
			t.Fatalf("message %d processed twice", r.MessageID)
		}
		seen[r.MessageID] = true
	}
	if len(seen) != count {
		t.Fatalf("processed %d messages, want %d", len(seen), count)
	}
}

func BenchmarkPoolSubmit(b *testing.B) {
	p := New(8, 64)
	for i := 0; i < b.N; i++ {
		_ = p.Submit(context.Background(), func(int) {})
	}
	p.Close()
}
