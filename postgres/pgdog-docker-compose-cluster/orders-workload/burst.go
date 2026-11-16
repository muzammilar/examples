package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"sort"
	"sync"
	"time"
)

type burstSample struct {
	latency float64
	err     error
}
type burstResult struct {
	Requests       int            `json:"requests"`
	Success        int            `json:"success"`
	Errors         map[string]int `json:"errors_by_sqlstate"`
	Elapsed        float64        `json:"elapsed_seconds"`
	SuccessRate    float64        `json:"successful_requests_per_second"`
	P50            float64        `json:"successful_p50_ms"`
	P99            float64        `json:"successful_p99_ms"`
	Max            float64        `json:"successful_max_ms"`
	PeakBackends   int            `json:"peak_primary_client_backends"`
	BackendSamples int            `json:"backend_samples"`
	MonitorErrors  int            `json:"monitor_errors"`
	FailedP50      float64        `json:"failed_p50_ms"`
	FailedP99      float64        `json:"failed_p99_ms"`
	FailedMax      float64        `json:"failed_max_ms"`
}

func burst(f fixture, route []int) error {
	customers := f.Customers
	requests, concurrency := number("BURST_REQUESTS", 4000), number("BURST_CLIENTS", 256)
	direct := os.Getenv("DIRECT_DSN")
	if direct == "" {
		return errors.New("DIRECT_DSN must identify the current primary")
	}
	ledger, err := history("/witness", route)
	if err != nil {
		return err
	}
	expected := customerTotals(ledger, customers)
	monitorDB, err := sql.Open("pgx", direct)
	if err != nil {
		return err
	}
	defer monitorDB.Close()
	monitorDB.SetMaxOpenConns(1)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	monitor, err := monitorDB.Conn(ctx)
	cancel()
	if err != nil {
		return err
	}
	defer monitor.Close()
	results := map[string]burstResult{}
	var unexpected error
	for _, mode := range []string{"direct_postgres", "pgdog_transaction_pool"} {
		dsn := direct
		if mode != "direct_postgres" {
			dsn = os.Getenv("DSN")
		}
		jobs := make(chan int)
		samples := make(chan burstSample, requests)
		stopMonitor := make(chan struct{})
		type observation struct{ peak, samples, errors int }
		peakDone := make(chan observation, 1)
		go func() {
			observed := observation{}
			ticker := time.NewTicker(20 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-stopMonitor:
					peakDone <- observed
					return
				case <-ticker.C:
					ctx, cancel := context.WithTimeout(context.Background(), time.Second)
					var n int
					e := monitor.QueryRowContext(ctx, "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND datname='postgres'").Scan(&n)
					cancel()
					if e != nil {
						observed.errors++
					} else {
						observed.samples++
						observed.peak = max(observed.peak, n)
					}
				}
			}
		}()
		var wg sync.WaitGroup
		started := time.Now()
		for w := 0; w < concurrency; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for request := range jobs {
					start := time.Now()
					db, e := sql.Open("pgx", dsn)
					if e == nil {
						db.SetMaxOpenConns(1)
						db.SetMaxIdleConns(0)
						ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
						var amount int64
						c := request % customers
						e = db.QueryRowContext(ctx, "SELECT coalesce(sum(amount),0) FROM orders WHERE customer_id=$1", c).Scan(&amount)
						cancel()
						db.Close()
						if e == nil && amount != expected[c] {
							e = fmt.Errorf("incorrect customer %d amount %d, expected %d", c, amount, expected[c])
						}
					}
					samples <- burstSample{float64(time.Since(start).Microseconds()) / 1000, e}
				}
			}()
		}
		go func() {
			defer close(jobs)
			for request := 0; request < requests; request++ {
				jobs <- request
			}
		}()
		wg.Wait()
		elapsed := time.Since(started)
		close(samples)
		close(stopMonitor)
		observed := <-peakDone
		result := burstResult{Requests: requests, Errors: map[string]int{}, Elapsed: elapsed.Seconds(), PeakBackends: observed.peak, BackendSamples: observed.samples, MonitorErrors: observed.errors}
		if observed.samples == 0 || observed.errors != 0 {
			unexpected = errors.New("backend monitor incomplete; inspect samples/errors")
		}
		latencies := []float64{}
		failed := []float64{}
		for sample := range samples {
			if sample.err == nil {
				result.Success++
				latencies = append(latencies, sample.latency)
			} else {
				failed = append(failed, sample.latency)
				code := "other"
				var state interface{ SQLState() string }
				if errors.As(sample.err, &state) {
					code = state.SQLState()
				}
				result.Errors[code]++
				if code != "53300" {
					unexpected = fmt.Errorf("%s unexpected request failure: %w", mode, sample.err)
				}
			}
		}
		result.P50, result.P99, result.Max = burstPercentiles(latencies)
		result.FailedP50, result.FailedP99, result.FailedMax = burstPercentiles(failed)
		if result.Success == 0 {
			unexpected = fmt.Errorf("%s served zero requests", mode)
		}
		result.SuccessRate = float64(result.Success) / elapsed.Seconds()
		results[mode] = result
	}
	data, err := json.MarshalIndent(map[string]any{"concurrent_clients": concurrency, "new_connection_per_request": true, "phases": results}, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	if err = save("/results/connection-burst.json", map[string]any{"concurrent_clients": concurrency, "new_connection_per_request": true, "phases": results}); err != nil {
		return err
	}
	if unexpected != nil {
		return unexpected
	}
	if results["pgdog_transaction_pool"].Success != requests {
		return errors.New("PgDog did not serve every request")
	}
	return nil
}

// The oracle covers every retained acknowledged allocation, not just the latest COUNT.
func customerTotals(l *ledger, customers int) []int64 {
	totals := make([]int64, customers)
	for _, run := range l.runs {
		for offset, flags := range run.flags {
			if flags&acknowledged != 0 {
				id := run.config.Base + int64(offset)
				totals[(offset/run.config.Batch)%customers] += id%10000 + 1
			}
		}
	}
	return totals
}
func burstPercentiles(values []float64) (float64, float64, float64) {
	if len(values) == 0 {
		return 0, 0, 0
	}
	sort.Float64s(values)
	return values[int(math.Ceil(float64(len(values))*.50))-1], values[int(math.Ceil(float64(len(values))*.99))-1], values[len(values)-1]
}
