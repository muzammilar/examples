package main

import (
	"context"
	"fmt"
	"path/filepath"
	"time"
)

func duckArchive(resume bool) error {
	n, e := positive("ROWS", 2000000)
	if e != nil {
		return e
	}
	customers, e := positive("CUSTOMERS", 10000)
	if e != nil {
		return e
	}
	batch, e := positive("BATCH_ROWS", 100000)
	if e != nil {
		return e
	}
	if n < 2 {
		return fmt.Errorf("archive requires at least two rows")
	}
	cut := n / 2
	db, e := duckAt(filepath.Join(env("RESULTS_DIR", "/results"), "duck-hot.duckdb"), true)
	if e != nil {
		return e
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()
	start := time.Now()
	if !resume {
		queries := []string{"CREATE SCHEMA IF NOT EXISTS lake.lab", "DROP TABLE IF EXISTS lake.lab.events", "CREATE TABLE lake.lab.events (event_id BIGINT,customer_id INTEGER,amount_cents BIGINT)", "DROP TABLE IF EXISTS hot", fmt.Sprintf("CREATE TABLE hot AS SELECT i AS event_id,CAST(i%%%d AS INTEGER) AS customer_id,i%%10000+1 AS amount_cents FROM range(%d) t(i)", customers, n)}
		for _, q := range queries {
			if _, e = db.ExecContext(ctx, q); e != nil {
				return e
			}
		}
	}
	if env("FAIL_AT", "") == "before-export" {
		return fmt.Errorf("injected before-export failure; local hot rows retained")
	}
	var existing int64
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM lake.lab.events").Scan(&existing); e != nil {
		return e
	}
	if existing == 0 {
		for lo := int64(0); lo < cut; lo += batch {
			hi := min(lo+batch, cut)
			if _, e = db.ExecContext(ctx, fmt.Sprintf("INSERT INTO lake.lab.events SELECT event_id,customer_id,amount_cents FROM hot WHERE event_id>=%d AND event_id<%d", lo, hi)); e != nil {
				return fmt.Errorf("unresolved export; hot data retained, no blind replay: %w", e)
			}
		}
	}
	if env("FAIL_AT", "") == "verification" {
		return fmt.Errorf("injected verification failure; hot rows retained")
	}
	// Independent DuckDB attachment and ClickHouse both inspect the committed cold table.
	if e = verifyRows(cut, customers); e != nil {
		return e
	}
	if env("FAIL_AT", "") == "after-export" {
		return fmt.Errorf("injected after-export failure; local hot rows retained")
	}
	tx, e := db.BeginTx(ctx, nil)
	if e != nil {
		return e
	}
	defer tx.Rollback()
	if _, e = tx.ExecContext(ctx, fmt.Sprintf("DELETE FROM hot WHERE event_id<%d", cut)); e != nil {
		return e
	}
	if e = tx.Commit(); e != nil {
		return e
	}
	var hot, count, unique, sum, bad int64
	if e = db.QueryRowContext(ctx, "SELECT count(*) FROM hot").Scan(&hot); e != nil {
		return e
	}
	q := fmt.Sprintf("SELECT count(*),count(DISTINCT event_id),sum(amount_cents),count(*) FILTER(WHERE event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR event_id<0 OR event_id>=%d OR customer_id!=event_id%%%d OR amount_cents!=event_id%%10000+1) FROM (SELECT * FROM hot UNION ALL SELECT * FROM lake.lab.events)", n, customers)
	if e = db.QueryRowContext(ctx, q).Scan(&count, &unique, &sum, &bad); e != nil {
		return e
	}
	expected := n/10000*50005000 + (n%10000)*(n%10000+1)/2
	if hot != n-cut || count != n || unique != n || sum != expected || bad != 0 {
		return fmt.Errorf("hot/cold oracle failed: %d/%d/%d/%d/%d", hot, count, unique, sum, bad)
	}
	return save("duck-archive", map[string]any{"rows": n, "archived_rows": cut, "hot_rows": hot, "resumed": resume, "seconds": time.Since(start).Seconds(), "missing_ids": 0, "duplicate_ids": 0})
}
