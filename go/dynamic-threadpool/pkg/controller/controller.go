// Package controller increases and decreases the size of a worker pool based
// on a simple metric: the depth of the pool's task queue.
package controller

import (
	"context"
	"log/slog"
	"time"
)

// Scaler is the subset of the pool API the controller needs.
type Scaler interface {
	Size() int
	QueueLen() int
	Bounds() (minSize, maxSize int)
	Grow(n int) error
	Shrink(n int) error
}

// Controller periodically inspects a pool and resizes it.
//
//   - If more than HighWater tasks are queued, it grows the pool by Step.
//   - If at most LowWater tasks are queued, it shrinks the pool by Step.
//
// Resizes are clamped to the pool's bounds.
type Controller struct {
	Pool      Scaler
	Interval  time.Duration
	HighWater int
	LowWater  int
	Step      int
	Logger    *slog.Logger
}

// Run blocks, adjusting the pool every Interval until ctx is cancelled.
func (c *Controller) Run(ctx context.Context) {
	ticker := time.NewTicker(c.Interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			c.Tick()
		}
	}
}

// Tick performs a single scaling decision. It returns the change in pool size
// (positive for growth, negative for shrinkage, zero for no change).
func (c *Controller) Tick() int {
	step := max(c.Step, 1)
	size, queued := c.Pool.Size(), c.Pool.QueueLen()
	minSize, maxSize := c.Pool.Bounds()

	var delta int
	switch {
	case queued > c.HighWater && size < maxSize:
		delta = min(step, maxSize-size)
		if err := c.Pool.Grow(delta); err != nil {
			c.log().Warn("grow failed", "err", err)
			return 0
		}
	case queued <= c.LowWater && size > minSize:
		delta = -min(step, size-minSize)
		if err := c.Pool.Shrink(-delta); err != nil {
			c.log().Warn("shrink failed", "err", err)
			return 0
		}
	default:
		return 0
	}
	c.log().Info("resized pool", "queued", queued, "from", size, "to", size+delta)
	return delta
}

func (c *Controller) log() *slog.Logger {
	if c.Logger == nil {
		return slog.Default()
	}
	return c.Logger
}
