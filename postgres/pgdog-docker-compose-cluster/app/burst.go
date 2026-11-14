package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
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
	Requests     int            `json:"requests"`
	Success      int            `json:"success"`
	Errors       map[string]int `json:"errors_by_sqlstate"`
	Elapsed      float64        `json:"elapsed_seconds"`
	SuccessRate  float64        `json:"successful_requests_per_second"`
	P50          float64        `json:"successful_p50_ms"`
	P99          float64        `json:"successful_p99_ms"`
	Max          float64        `json:"successful_max_ms"`
	PeakBackends int            `json:"peak_primary_client_backends"`
}

func burst(count, customers, batch int) error {
	requests, concurrency := number("BURST_REQUESTS", 4000), number("BURST_CLIENTS", 256)
	direct := os.Getenv("DIRECT_DSN")
	if direct == "" {
		return errors.New("DIRECT_DSN must identify the current primary")
	}
	expected := make([]int64, customers)
	for id := 0; id < count; id++ {
		expected[(id/batch)%customers] += int64(id%10000 + 1)
	}
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
	for _, mode := range []string{"direct_postgres", "pgdog_transaction_pool"} {
		dsn := direct
		if mode != "direct_postgres" {
			dsn = os.Getenv("DSN")
		}
		jobs := make(chan int)
		samples := make(chan burstSample, requests)
		stopMonitor := make(chan struct{})
		peakDone := make(chan int, 1)
		go func() {
			peak := 0
			ticker := time.NewTicker(20 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-stopMonitor:
					peakDone <- peak
					return
				case <-ticker.C:
					ctx, cancel := context.WithTimeout(context.Background(), time.Second)
					var n int
					e := monitor.QueryRowContext(ctx, "SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND datname='postgres'").Scan(&n)
					cancel()
					if e == nil && n > peak {
						peak = n
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
		result := burstResult{Requests: requests, Errors: map[string]int{}, Elapsed: elapsed.Seconds(), PeakBackends: <-peakDone}
		latencies := []float64{}
		for sample := range samples {
			if sample.err == nil {
				result.Success++
				latencies = append(latencies, sample.latency)
			} else {
				code := "other"
				var state interface{ SQLState() string }
				if errors.As(sample.err, &state) {
					code = state.SQLState()
				}
				result.Errors[code]++
				if code != "53300" {
					return fmt.Errorf("%s unexpected request failure: %w", mode, sample.err)
				}
			}
		}
		if len(latencies) == 0 {
			return fmt.Errorf("%s served zero requests", mode)
		}
		sort.Float64s(latencies)
		n := len(latencies)
		result.P50 = latencies[(n-1)/2]
		result.P99 = latencies[(n-1)*99/100]
		result.Max = latencies[n-1]
		result.SuccessRate = float64(result.Success) / elapsed.Seconds()
		results[mode] = result
	}
	data, err := json.MarshalIndent(map[string]any{"concurrent_clients": concurrency, "new_connection_per_request": true, "phases": results}, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	if err = os.WriteFile("/results/connection-burst.json", append(data, '\n'), 0644); err != nil {
		return err
	}
	if results["pgdog_transaction_pool"].Success != requests {
		return errors.New("PgDog did not serve every request")
	}
	return nil
}
