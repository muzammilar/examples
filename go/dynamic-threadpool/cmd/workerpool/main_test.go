package main

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus"

	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/metrics"
	"github.com/muzammilar/examples-go/dynamic-threadpool/pkg/workerpool"
)

func newPool(t *testing.T) (*workerpool.Pool, *metrics.Metrics, *prometheus.Registry) {
	t.Helper()
	pool, err := workerpool.New(context.Background(), workerpool.Config{Min: 1, Max: 4, QueueSize: 16})
	if err != nil {
		t.Fatal(err)
	}
	reg := prometheus.NewRegistry()
	m, err := metrics.New(reg, pool)
	if err != nil {
		t.Fatal(err)
	}
	return pool, m, reg
}

// With duration 0, produce runs until the context is cancelled.
func TestProduceZeroDurationRunsUntilCancel(t *testing.T) {
	pool, m, _ := newPool(t)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	var submitted, processed atomic.Int64
	go func() {
		defer close(done)
		produce(ctx, pool, m, 0, 20*time.Millisecond, time.Millisecond, &submitted, &processed)
	}()

	select {
	case <-done:
		t.Fatal("produce returned before cancel with duration 0")
	case <-time.After(150 * time.Millisecond):
	}
	cancel()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("produce did not return after cancel")
	}
	pool.Close()
	if submitted.Load() == 0 || processed.Load() != submitted.Load() {
		t.Fatalf("submitted=%d processed=%d", submitted.Load(), processed.Load())
	}
}

func TestProduceStopsAfterDuration(t *testing.T) {
	pool, m, _ := newPool(t)
	defer pool.Close()
	var submitted, processed atomic.Int64
	start := time.Now()
	produce(context.Background(), pool, m, 100*time.Millisecond, 20*time.Millisecond, time.Millisecond, &submitted, &processed)
	if el := time.Since(start); el > time.Second {
		t.Fatalf("produce took %v, want ~100ms", el)
	}
}

func TestServeMetrics(t *testing.T) {
	pool, m, reg := newPool(t)
	defer pool.Close()
	m.Submitted()
	quiet := slog.New(slog.NewTextHandler(io.Discard, nil))

	srv, addr, err := serveMetrics("127.0.0.1:0", reg, quiet)
	if err != nil {
		t.Fatal(err)
	}
	defer srv.Close()

	resp, err := http.Get("http://" + addr + "/metrics")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	for _, want := range []string{
		"workerpool_size 1",
		"workerpool_min_workers 1",
		"workerpool_max_workers 4",
		"workerpool_tasks_submitted_total 1",
		`workerpool_scale_events_total{direction="grow"} 0`,
	} {
		if !strings.Contains(string(body), want) {
			t.Errorf("/metrics missing %q", want)
		}
	}

	if _, _, err := serveMetrics("not-an-address", reg, quiet); err == nil {
		t.Fatal("expected an error for an invalid address")
	}
}
