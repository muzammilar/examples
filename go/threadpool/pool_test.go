package main

import (
	"context"
	"errors"
	"fmt"
	"runtime"
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

func TestNewRejectsInvalidArgs(t *testing.T) {
	cases := []struct {
		name        string
		size, queue int
	}{
		{"zero size", 0, 1},
		{"negative size", -1, 1},
		{"negative queue", 1, -1},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			defer func() {
				if recover() == nil {
					t.Fatalf("New(%d, %d) did not panic", tc.size, tc.queue)
				}
			}()
			New(tc.size, tc.queue)
		})
	}
}

// Close must wait for a Submit that is blocked on a full queue, and that task
// must still run (Close drains, it does not drop queued work).
func TestCloseWhileSubmitBlocked(t *testing.T) {
	p := New(1, 0)
	release := make(chan struct{})
	started := make(chan struct{})
	if err := p.Submit(context.Background(), func(int) { close(started); <-release }); err != nil {
		t.Fatal(err)
	}
	<-started

	var ran atomic.Bool
	submitErr := make(chan error, 1)
	go func() {
		submitErr <- p.Submit(context.Background(), func(int) { ran.Store(true) })
	}()

	closed := make(chan struct{})
	go func() {
		time.Sleep(10 * time.Millisecond) // let the Submit above block first
		p.Close()
		close(closed)
	}()

	select {
	case <-closed:
		t.Fatal("Close returned while a worker was still busy")
	case <-time.After(30 * time.Millisecond):
	}
	close(release)

	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("Close did not return after the worker was released")
	}
	switch err := <-submitErr; {
	case err == nil:
		if !ran.Load() {
			t.Fatal("blocked Submit succeeded but its task never ran")
		}
	case errors.Is(err, ErrPoolClosed):
		// Submit lost the race with Close; also acceptable.
	default:
		t.Fatalf("unexpected Submit error: %v", err)
	}
}

func TestProduceStopsOnClosedPool(t *testing.T) {
	p := New(2, 2)
	p.Close()
	results := make(chan Result, 10)
	if err := produce(context.Background(), p, 10, 2, results); !errors.Is(err, ErrPoolClosed) {
		t.Fatalf("got %v, want ErrPoolClosed", err)
	}
}

// BenchmarkPoolSubmit measures Submit throughput (a no-op task) across pool
// and queue sizes. The time includes draining the queue in Close.
func BenchmarkPoolSubmit(b *testing.B) {
	sizes := []int{1, 4, 16, runtime.GOMAXPROCS(0)}
	queues := []int{0, 64, 1024}
	for _, size := range sizes {
		for _, queue := range queues {
			b.Run(fmt.Sprintf("workers=%d/queue=%d", size, queue), func(b *testing.B) {
				b.ReportAllocs()
				p := New(size, queue)
				task := func(int) {}
				ctx := context.Background()
				b.ResetTimer()
				for i := 0; i < b.N; i++ {
					_ = p.Submit(ctx, task)
				}
				p.Close()
			})
		}
	}
}

// BenchmarkPoolSubmitParallel measures Submit under contention from many
// producer goroutines.
func BenchmarkPoolSubmitParallel(b *testing.B) {
	for _, size := range []int{1, 4, 16, runtime.GOMAXPROCS(0)} {
		b.Run(fmt.Sprintf("workers=%d", size), func(b *testing.B) {
			b.ReportAllocs()
			p := New(size, 64)
			task := func(int) {}
			b.ResetTimer()
			b.RunParallel(func(pb *testing.PB) {
				ctx := context.Background()
				for pb.Next() {
					_ = p.Submit(ctx, task)
				}
			})
			p.Close()
		})
	}
}

// BenchmarkDemoWorkload runs the demo end to end (4 producers, message
// generation, checksum, result aggregation); one op is one message.
func BenchmarkDemoWorkload(b *testing.B) {
	for _, size := range []int{1, 4, 16, runtime.GOMAXPROCS(0)} {
		b.Run(fmt.Sprintf("workers=%d", size), func(b *testing.B) {
			b.ReportAllocs()
			p := New(size, 64)
			results := make(chan Result, 64)
			done := make(chan struct{})
			go func() {
				defer close(done)
				for range results {
				}
			}()
			b.ResetTimer()
			if err := produce(context.Background(), p, b.N, 4, results); err != nil {
				b.Fatal(err)
			}
			p.Close()
			close(results)
			<-done
		})
	}
}
