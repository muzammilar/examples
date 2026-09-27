package controller

import (
	"context"
	"testing"
	"time"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

type fakePool struct {
	size, queued, min, max int
}

func (f *fakePool) Size() int                      { return f.size }
func (f *fakePool) QueueLen() int                  { return f.queued }
func (f *fakePool) Bounds() (minSize, maxSize int) { return f.min, f.max }
func (f *fakePool) Grow(n int) error               { f.size += n; return nil }
func (f *fakePool) Shrink(n int) error             { f.size -= n; return nil }

func TestTick(t *testing.T) {
	tests := []struct {
		name         string
		size, queued int
		wantDelta    int
	}{
		{"grow on backlog", 2, 10, 3},
		{"grow clamped to max", 7, 10, 1},
		{"hold at max", 8, 10, 0},
		{"hold between watermarks", 4, 3, 0},
		{"shrink when idle", 5, 0, -3},
		{"shrink clamped to min", 2, 0, -1},
		{"hold at min", 1, 0, 0},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			f := &fakePool{size: tt.size, queued: tt.queued, min: 1, max: 8}
			c := &Controller{Pool: f, HighWater: 5, LowWater: 0, Step: 3}
			if got := c.Tick(); got != tt.wantDelta {
				t.Fatalf("delta %d, want %d", got, tt.wantDelta)
			}
			if f.size != tt.size+tt.wantDelta {
				t.Fatalf("size %d, want %d", f.size, tt.size+tt.wantDelta)
			}
		})
	}
}

// TestRunScalesRealPool drives a real pool: a backlog should grow it, and an
// empty queue should shrink it back to the minimum.
func TestRunScalesRealPool(t *testing.T) {
	pool, err := workerpool.New(context.Background(), workerpool.Config{Min: 1, Max: 4, QueueSize: 100})
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()

	release := make(chan struct{})
	for i := 0; i < 20; i++ {
		if err := pool.Submit(context.Background(), func(context.Context) { <-release }); err != nil {
			t.Fatal(err)
		}
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	c := &Controller{Pool: pool, Interval: time.Millisecond, HighWater: 2, LowWater: 0, Step: 1}
	go c.Run(ctx)

	waitFor(t, func() bool { return pool.Size() == 4 })
	close(release)
	waitFor(t, func() bool { return pool.Size() == 1 })
}

func waitFor(t *testing.T, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(2 * time.Second)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not met in time")
		}
		time.Sleep(time.Millisecond)
	}
}
