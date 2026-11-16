package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"

	"sort"
	"sync"
	"time"
)

const dashboardSQL = "SELECT c.customer_id,count(*),sum(o.amount) FROM customers c JOIN orders o USING(customer_id)"

type analyticsPhase struct {
	Operations int       `json:"operations"`
	Elapsed    float64   `json:"elapsed_seconds"`
	Rate       float64   `json:"operations_per_second"`
	P50        float64   `json:"p50_ms"`
	P99        float64   `json:"p99_ms"`
	Max        float64   `json:"max_ms"`
	Samples    []float64 `json:"latency_samples_ms"`
}

func analyticsStats(samples []float64, elapsed time.Duration) analyticsPhase {
	raw := append([]float64(nil), samples...)
	sort.Float64s(samples)
	n := len(samples)
	return analyticsPhase{Operations: n, Elapsed: elapsed.Seconds(), Rate: float64(n) / elapsed.Seconds(), P50: samples[int(math.Ceil(float64(n)*.50))-1], P99: samples[int(math.Ceil(float64(n)*.99))-1], Max: samples[n-1], Samples: raw}
}

func analytics(db *sql.DB, f fixture, route []int) error {
	customers, workers := f.Customers, number("WORKERS", 8)
	var config profileConfig
	if err := readJSON("/results/analytics.json", &config); err != nil {
		return err
	}
	journal, err := openJournal("profiles.jsonl")
	if err != nil {
		return err
	}
	defer journal.file.Close()
	queries := number("ANALYTICS_QUERIES", 25)
	requests := number("ANALYTICS_REQUESTS", 200)
	if requests > customers || queries > customers {
		return fmt.Errorf("analytics queries and requests must not exceed CUSTOMERS")
	}
	expectedCount := make([]int, customers)
	expectedSum := make([]int64, customers)
	var totalSum int64
	ledger, err := history("/witness", route)
	if err != nil {
		return err
	}
	count := 0
	for _, run := range ledger.runs {
		for offset, flags := range run.flags {
			if flags&acknowledged != 0 {
				id := run.config.Base + int64(offset)
				c := (offset / run.config.Batch) % customers
				expectedCount[c]++
				expectedSum[c] += id%10000 + 1
				totalSum += id%10000 + 1
				count++
			}
		}
	}
	for c, n := range expectedCount {
		if n == 0 {
			return fmt.Errorf("customer %d has no acknowledged orders", c)
		}
	}
	global := dashboardSQL + " GROUP BY c.customer_id"
	local := dashboardSQL + " WHERE c.customer_id=$1 GROUP BY c.customer_id"
	plans := map[string]json.RawMessage{}
	taskCounts := map[string]int{}
	for name, query := range map[string]string{"tenant": local, "all_tenants": global} {
		var data []byte
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		var err error
		if name == "tenant" {
			err = db.QueryRowContext(ctx, "EXPLAIN (FORMAT JSON) "+query, 0).Scan(&data)
		} else {
			err = db.QueryRowContext(ctx, "EXPLAIN (FORMAT JSON) "+query).Scan(&data)
		}
		cancel()
		if err != nil {
			return err
		}
		var decoded any
		if err = json.Unmarshal(data, &decoded); err != nil {
			return err
		}
		var walk func(any)
		walk = func(value any) {
			switch x := value.(type) {
			case map[string]any:
				for k, v := range x {
					if k == "Task Count" {
						if n, ok := v.(float64); ok && int(n) > taskCounts[name] {
							taskCounts[name] = int(n)
						}
					}
					walk(v)
				}
			case []any:
				for _, v := range x {
					walk(v)
				}
			}
		}
		walk(decoded)
		plans[name] = data
	}
	if taskCounts["tenant"] != 1 || taskCounts["all_tenants"] <= 1 {
		return fmt.Errorf("expected one routed task versus distributed tasks: %v", taskCounts)
	}
	phases := map[string]analyticsPhase{}
	for _, mode := range []string{"all_tenants_then_select", "tenant_filter"} {
		samples := make([]float64, 0, queries)
		phaseStart := time.Now()
		for c := 0; c < queries; c++ {
			start := time.Now()
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
			var err error
			if mode == "tenant_filter" {
				var gotC, n int
				var sum int64
				err = db.QueryRowContext(ctx, local, c).Scan(&gotC, &n, &sum)
				if err == nil && (gotC != c || n != expectedCount[c] || sum != expectedSum[c]) {
					err = fmt.Errorf("tenant %d dashboard mismatch", c)
				}
			} else {
				var rows *sql.Rows
				rows, err = db.QueryContext(ctx, global)
				if err == nil {
					seen := 0
					found := false
					total := 0
					var sumAll int64
					for rows.Next() {
						var gotC, n int
						var sum int64
						if err = rows.Scan(&gotC, &n, &sum); err != nil {
							break
						}
						if gotC < 0 || gotC >= customers || n != expectedCount[gotC] || sum != expectedSum[gotC] {
							err = fmt.Errorf("global dashboard mismatch")
							break
						}
						seen++
						total += n
						sumAll += sum
						if gotC == c {
							found = true
						}
					}
					if err == nil {
						err = rows.Err()
					}
					rows.Close()
					if err == nil && (!found || seen != customers || total != count || sumAll != totalSum) {
						err = fmt.Errorf("global dashboard incomplete")
					}
				}
			}
			cancel()
			if err != nil {
				return err
			}
			samples = append(samples, float64(time.Since(start).Microseconds())/1000)
		}
		phases[mode] = analyticsStats(samples, time.Since(phaseStart))
	}
	// A tenant-scoped transaction changes the profile and reads its colocated orders.
	jobs := make(chan int)
	samples := make(chan float64, requests)
	errs := make(chan error, workers)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for c := range jobs {
				started := time.Now()
				qctx, qcancel := context.WithTimeout(ctx, 30*time.Second)
				var gotC, n int
				var sum int64
				var err error
				uncertain := false
				mutation := event{Stage: "attempt", First: config.Version, Count: 1, Customer: c, Shard: route[c], Started: time.Now().UTC()}
				if c%5 == 0 {
					if err = journal.append(mutation); err != nil {
						errs <- err
						cancel()
						qcancel()
						return
					}
					var tx *sql.Tx
					tx, err = db.BeginTx(qctx, nil)
					if err == nil {
						_, err = tx.ExecContext(qctx, "INSERT INTO customer_profiles(customer_id,version,display_name) VALUES($1,$2,$3) ON CONFLICT(customer_id) DO UPDATE SET version=excluded.version,display_name=excluded.display_name WHERE customer_profiles.version<excluded.version", c, config.Version, profileName(config.Version, c))
						if err == nil {
							err = tx.QueryRowContext(qctx, local, c).Scan(&gotC, &n, &sum)
						}
						if err == nil && (gotC != c || n != expectedCount[c] || sum != expectedSum[c]) {
							err = fmt.Errorf("transaction dashboard mismatch")
						}
						if err == nil {
							err = tx.Commit()
							uncertain = err != nil
						} else {
							_ = tx.Rollback()
						}
					}
				} else {
					err = db.QueryRowContext(qctx, local, c).Scan(&gotC, &n, &sum)
					if err == nil && (gotC != c || n != expectedCount[c] || sum != expectedSum[c]) {
						err = fmt.Errorf("mixed dashboard mismatch")
					}
				}
				qcancel()
				if c%5 == 0 {
					mutation.At = time.Now().UTC()
					mutation.Latency = float64(time.Since(started)) / float64(time.Millisecond)
					mutation.Stage = "ack"
					if err != nil {
						mutation.Stage = "error"
						mutation.Error = err.Error()
						mutation.Ambiguous = uncertain
					}
					if e := journal.append(mutation); e != nil {
						errs <- e
						cancel()
						return
					}
				}
				if err != nil {
					errs <- err
					cancel()
					return
				}
				samples <- float64(time.Since(started).Microseconds()) / 1000
			}
		}()
	}
	go func() {
		defer close(jobs)
		for c := 0; c < requests; c++ {
			select {
			case jobs <- c:
			case <-ctx.Done():
				return
			}
		}
	}()
	wg.Wait()
	close(samples)
	select {
	case err := <-errs:
		return err
	default:
	}
	durations := make([]float64, 0, requests)
	for sample := range samples {
		durations = append(durations, sample)
	}
	if len(durations) != requests {
		return fmt.Errorf("incomplete mixed workload")
	}
	phases["mixed_tenant_transactions"] = analyticsStats(durations, time.Since(start))
	changed := 0
	for c := 0; c < requests; c += 5 {
		var version int64
		var name string
		if err := db.QueryRow("SELECT version,display_name FROM customer_profiles WHERE customer_id=$1", c).Scan(&version, &name); err != nil {
			return err
		}
		if version != config.Version || name != profileName(config.Version, c) {
			return fmt.Errorf("profile %d mismatch", c)
		}
		changed++
	}
	result := map[string]any{"orders": count, "customers": customers, "profile_updates": changed, "task_counts": taskCounts, "phases": phases, "plans": plans}
	return save("/results/tenant-analytics.json", result)
}
