package main

import (
	"context"
	"database/sql"
	"fmt"
	"math"
	"sort"
	"time"
)

// The fixed old IDs are persisted-data reads, not a claimed physical cold cache.
func queryTrials(ctx context.Context, db *sql.DB, batch string, count, customers int64) error {
	for trial := 0; trial < 3; trial++ {
		kinds := []string{"point", "regional-rollup"}
		if trial%2 == 1 {
			kinds[0], kinds[1] = kinds[1], kinds[0]
		}
		for _, kind := range kinds {
			values := make([]float64, 0, 30)
			for attempt := 0; attempt < 35; attempt++ {
				started := time.Now()
				if kind == "point" {
					id := int64(1 + (attempt % 8))
					if id > count {
						id = count
					}
					var customer, amount int64
					if e := db.QueryRowContext(ctx, "SELECT customer_id,amount_cents FROM pipeline.orders WHERE batch=? AND order_id=?", batch, id).Scan(&customer, &amount); e != nil {
						return e
					}
					if customer != 1+(id-1)%customers || amount != 100+id%1000 {
						return fmt.Errorf("point read mismatch")
					}
				} else {
					var n, amount int64
					if e := db.QueryRowContext(ctx, "SELECT sum(orders),sum(amount_cents) FROM pipeline.regions WHERE batch=?", batch).Scan(&n, &amount); e != nil {
						return e
					}
					cycles, tail := count/1000, count%1000
					expected := 100*count + cycles*499500 + tail*(tail+1)/2
					if n != count || amount != expected {
						return fmt.Errorf("aggregate mismatch")
					}
				}
				elapsed := float64(time.Since(started).Nanoseconds()) / 1e6
				if attempt >= 5 {
					values = append(values, elapsed)
				}
				time.Sleep(100 * time.Millisecond)
			}
			sort.Float64s(values)
			sum := 0.0
			for _, v := range values {
				sum += v
			}
			percentile := func(p float64) float64 { return values[int(math.Ceil(p*float64(len(values))))-1] }
			emit(map[string]any{"trial": trial + 1, "query": kind, "measured": len(values), "warmup": 5, "think_ms": 100, "cache": "warm; physical cold unproven", "mean_ms": sum / float64(len(values)), "p50_ms": percentile(.5), "p95_ms": percentile(.95), "p99_ms": percentile(.99), "max_ms": values[len(values)-1], "p999_ms": nil})
		}
	}
	return nil
}
