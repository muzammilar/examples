package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"sync"
	"time"
)

const dashboardSQL = "SELECT c.customer_id,count(*),sum(o.amount) FROM customers c JOIN orders o USING(customer_id)"

type analyticsPhase struct {
	Operations int     `json:"operations"`
	Elapsed    float64 `json:"elapsed_seconds"`
	Rate       float64 `json:"operations_per_second"`
	P50        float64 `json:"p50_ms"`
	P99        float64 `json:"p99_ms"`
	Max        float64 `json:"max_ms"`
}

func analyticsStats(samples []float64, elapsed time.Duration) analyticsPhase {
	sort.Float64s(samples)
	n := len(samples)
	return analyticsPhase{n, elapsed.Seconds(), float64(n) / elapsed.Seconds(), samples[(n-1)/2], samples[(n-1)*99/100], samples[n-1]}
}

func analytics(db *sql.DB, count, customers, batch, workers int) error {
	queries := number("ANALYTICS_QUERIES", 25)
	requests := number("ANALYTICS_REQUESTS", 200)
	if count < customers*batch {
		return fmt.Errorf("tenant analytics requires COUNT >= CUSTOMERS * BATCH")
	}
	if requests > customers || queries > customers {
		return fmt.Errorf("analytics queries and requests must not exceed CUSTOMERS")
	}
	expectedCount := make([]int, customers)
	expectedSum := make([]int64, customers)
	var totalSum int64
	for id := 0; id < count; id++ {
		c := (id / batch) % customers
		expectedCount[c]++
		expectedSum[c] += int64(id%10000 + 1)
		totalSum += int64(id%10000 + 1)
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
	if _, err := db.Exec("UPDATE customers SET name='inactive'"); err != nil {
		return err
	}
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
				if c%5 == 0 {
					var tx *sql.Tx
					tx, err = db.BeginTx(qctx, nil)
					if err == nil {
						_, err = tx.ExecContext(qctx, "UPDATE customers SET name=$1 WHERE customer_id=$2", fmt.Sprintf("active-%d", c), c)
						if err == nil {
							err = tx.QueryRowContext(qctx, local, c).Scan(&gotC, &n, &sum)
						}
						if err == nil && (gotC != c || n != expectedCount[c] || sum != expectedSum[c]) {
							err = fmt.Errorf("transaction dashboard mismatch")
						}
						if err == nil {
							err = tx.Commit()
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
	var changed int
	if err := db.QueryRow("SELECT count(*) FROM customers WHERE name LIKE 'active-%'").Scan(&changed); err != nil {
		return err
	}
	if changed != (requests+4)/5 {
		return fmt.Errorf("profile changes %d", changed)
	}
	result := map[string]any{"orders": count, "customers": customers, "profile_updates": changed, "task_counts": taskCounts, "phases": phases, "plans": plans}
	data, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return os.WriteFile("/results/tenant-analytics.json", append(data, '\n'), 0644)
}
