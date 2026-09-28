package controller

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"sync"
	"testing"
	"time"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

var discard = slog.New(slog.NewTextHandler(io.Discard, nil))

// BenchmarkTick measures the cost of a single scaling decision against a fake
// pool, for each outcome.
func BenchmarkTick(b *testing.B) {
	cases := []struct {
		name         string
		size, queued int
	}{
		{"hold", 4, 3},
		{"grow", 4, 10},
		{"shrink", 4, 0},
	}
	for _, tc := range cases {
		b.Run(tc.name, func(b *testing.B) {
			b.ReportAllocs()
			f := &fakePool{min: 1, max: 8}
			c := &Controller{Pool: f, HighWater: 5, LowWater: 0, Step: 1, Logger: discard}
			for i := 0; i < b.N; i++ {
				f.size, f.queued = tc.size, tc.queued // reset state each decision
				c.Tick()
			}
		})
	}
}

// BenchmarkSubmitWhileScaling measures Submit throughput on a real pool while
// the controller is resizing it. Tasks do a small amount of work so the queue
// fills and the controller actually grows and shrinks the pool.
func BenchmarkSubmitWhileScaling(b *testing.B) {
	for _, interval := range []time.Duration{100 * time.Microsecond, time.Millisecond} {
		b.Run(fmt.Sprintf("interval=%s", interval), func(b *testing.B) {
			b.ReportAllocs()
			pool, err := workerpool.New(context.Background(), workerpool.Config{Min: 1, Max: 32, QueueSize: 64})
			if err != nil {
				b.Fatal(err)
			}
			c := &Controller{Pool: pool, Interval: interval, HighWater: 8, LowWater: 0, Step: 2, Logger: discard}
			ctx, cancel := context.WithCancel(context.Background())
			var wg sync.WaitGroup
			wg.Add(1)
			go func() {
				defer wg.Done()
				c.Run(ctx)
			}()

			task := func(context.Context) {
				deadline := time.Now().Add(time.Microsecond)
				for time.Now().Before(deadline) { // short busy task
				}
			}
			b.ResetTimer()
			for i := 0; i < b.N; i++ {
				if err := pool.Submit(context.Background(), task); err != nil {
					b.Fatal(err)
				}
			}
			pool.Close()
			b.StopTimer()
			cancel()
			wg.Wait()
		})
	}
}
