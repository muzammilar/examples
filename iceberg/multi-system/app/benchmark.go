package main

import (
	"context"
	"database/sql"
	"fmt"
	"sort"
	"time"
)

func benchmark() error {
	n, err := positive("ROWS", 2000000)
	if err != nil {
		return err
	}
	customers, err := positive("CUSTOMERS", 10000)
	if err != nil {
		return err
	}
	runs, err := positive("QUERY_RUNS", 20)
	if err != nil {
		return err
	}
	if runs > 1000 {
		return fmt.Errorf("QUERY_RUNS must be <=1000")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	c, err := ch()
	if err != nil {
		return err
	}
	defer c.Close()
	native := env("BENCH_NATIVE", "false") == "true"
	table := "lake.`lab.events`"
	if native {
		table = "default.serving_current"
	} else if err = catalog(ctx, c); err != nil {
		return err
	}
	var db *sql.DB
	if !native {
		db, err = duck(true)
		if err != nil {
			return err
		}
		defer db.Close()
	}
	results := map[string]any{}
	customer := min(int64(42), customers-1)
	for _, filtered := range []bool{false, true} {
		where := ""
		name := "aggregate"
		var count, sum int64
		if filtered {
			where = fmt.Sprintf(" WHERE customer_id=%d", customer)
			name = "customer"
			for i := customer; i < n; i += customers {
				count++
				sum += i%10000 + 1
			}
		} else {
			count = n
			sum = n/10000*50005000 + (n%10000)*(n%10000+1)/2
		}
		engines := []string{"clickhouse"}
		if !native {
			engines = append(engines, "duckdb")
		}
		for _, engine := range engines {
			samples := []float64{}
			var query func() (int64, int64, error)
			if engine == "duckdb" {
				query = func() (int64, int64, error) {
					var a, b int64
					e := db.QueryRowContext(ctx, "SELECT count(*),sum(amount_cents) FROM lake.lab.events"+where).Scan(&a, &b)
					return a, b, e
				}
			} else {
				query = func() (int64, int64, error) {
					var a uint64
					var b int64
					e := c.QueryRow(ctx, "SELECT count(),sum(amount_cents) FROM "+table+where+" LIMIT 1 SETTINGS use_query_cache=0").Scan(&a, &b)
					return int64(a), b, e
				}
			}
			for trial := int64(0); trial < runs+2; trial++ {
				start := time.Now()
				a, b, e := query()
				elapsed := time.Since(start).Seconds()
				if e != nil {
					return e
				}
				if a != count || b != sum {
					return fmt.Errorf("benchmark oracle %s/%s: %d/%d want %d/%d", engine, name, a, b, count, sum)
				}
				if trial >= 2 {
					samples = append(samples, elapsed)
				}
			}
			ordered := append([]float64(nil), samples...)
			sort.Float64s(ordered)
			results[engine+"/"+name] = map[string]any{"seconds": samples, "p50_seconds": ordered[(len(ordered)-1)/2], "p99_seconds": ordered[min(len(ordered)-1, int(float64(len(ordered))*0.99))], "rows": count, "amount_cents": sum}
		}
	}
	return save("benchmark", map[string]any{"rows": n, "query_runs": runs, "warmups": 2, "native": native, "queries": results, "cache": "warm OS/object caches; ClickHouse result cache disabled"})
}
