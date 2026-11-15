package main

import (
	"context"
	"fmt"
	"sort"
	"time"
)

func benchmark(ctx context.Context) error {
	n, e := number("ROWS", 2000000)
	if e != nil {
		return e
	}
	customers, e := number("CUSTOMERS", 10000)
	if e != nil {
		return e
	}
	runs, e := number("QUERY_RUNS", 20)
	if e != nil {
		return e
	}
	if runs > 1000 {
		return fmt.Errorf("QUERY_RUNS must be <=1000")
	}
	type target struct {
		name, table string
		lo, hi      int64
	}
	targets := []target{{"direct", "lake.`lab.events`", 0, n}}
	if env("BENCH_MODE", "direct") == "archive" {
		targets = []target{{"cold", "lake.`lab.events`", 0, n / 2}, {"hot", "default.hot", n / 2, n}, {"union", "(SELECT event_id,customer_id,amount_cents FROM default.hot WHERE event_day>=toDate('2099-01-01') UNION ALL SELECT event_id,customer_id,amount_cents FROM lake.`lab.events`)", 0, n}}
	}
	results := map[string]any{}
	for _, t := range targets {
		if e = check(ctx, t.table, t.lo, t.hi, customers); e != nil {
			return e
		}
		for _, filtered := range []bool{false, true} {
			where := ""
			name := "aggregate"
			count, sum := t.hi-t.lo, total(t.hi)-total(t.lo)
			if filtered {
				customer := min(int64(42), customers-1)
				where = fmt.Sprintf(" WHERE customer_id=%d", customer)
				name = "customer"
				count = 0
				sum = 0
				first := t.lo + (customer-t.lo%customers+customers)%customers
				for i := first; i < t.hi; i += customers {
					count++
					sum += i%10000 + 1
				}
			}
			want := fmt.Sprintf("%d\t%d", count, sum)
			samples := []float64{}
			for trial := int64(0); trial < runs+2; trial++ {
				start := time.Now()
				value, err := sql(ctx, "SELECT count(),sum(amount_cents) FROM "+t.table+where+" LIMIT 1 SETTINGS use_query_cache=0 FORMAT TabSeparated")
				elapsed := time.Since(start).Seconds()
				if err != nil {
					return err
				}
				if value != want {
					return fmt.Errorf("benchmark oracle %s/%s: %q want %q", t.name, name, value, want)
				}
				if trial >= 2 {
					samples = append(samples, elapsed)
				}
			}
			ordered := append([]float64(nil), samples...)
			sort.Float64s(ordered)
			results[t.name+"/"+name] = map[string]any{"seconds": samples, "p50_seconds": ordered[(len(ordered)-1)/2], "p99_seconds": ordered[min(len(ordered)-1, int(float64(len(ordered))*0.99))], "rows": count, "amount_cents": sum}
		}
	}
	return output("benchmark", map[string]any{"rows": n, "query_runs": runs, "warmups": 2, "queries": results, "cache": "warm OS/object caches; result cache disabled"})
}
