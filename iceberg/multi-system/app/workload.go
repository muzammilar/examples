package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
)

func positive(key string, fallback int64) (int64, error) {
	n, err := strconv.ParseInt(env(key, strconv.FormatInt(fallback, 10)), 10, 64)
	if err != nil || n < 1 || n > 100000000 {
		return 0, fmt.Errorf("%s must be between 1 and 100000000", key)
	}
	return n, nil
}
func catalog(ctx context.Context, c clickhouse.Conn) error {
	return c.Exec(ctx, "CREATE DATABASE IF NOT EXISTS lake ENGINE=DataLakeCatalog("+quote(env("CATALOG_URL", "http://storage:8181"))+", 'demo', 'demo') SETTINGS catalog_type='rest', catalog_credential='demo:demo', vended_credentials=false, warehouse='warehouse', storage_endpoint='http://storage:8333/warehouse'")
}
func save(name string, v any) error {
	p := filepath.Join(env("RESULTS_DIR", "/results"), name+".json")
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(p, append(b, '\n'), 0644)
}

// Every acknowledgement is flushed outside the object store. Failed appends are not retried.
func load() error {
	n, err := positive("ROWS", 2000000)
	if err != nil {
		return err
	}
	customers, err := positive("CUSTOMERS", 10000)
	if err != nil {
		return err
	}
	batch, err := positive("BATCH_ROWS", 100000)
	if err != nil {
		return err
	}
	db, err := duck(true)
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	for _, q := range []string{"CREATE SCHEMA IF NOT EXISTS lake.lab", "DROP TABLE IF EXISTS lake.lab.events", "CREATE TABLE lake.lab.events (event_id BIGINT, customer_id INTEGER, amount_cents BIGINT)"} {
		if _, err = db.ExecContext(ctx, q); err != nil {
			return err
		}
	}
	journal, err := os.OpenFile(filepath.Join(env("RESULTS_DIR", "/results"), "acknowledgements.jsonl"), os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer journal.Close()
	start := time.Now()
	for lo := int64(0); lo < n; lo += batch {
		hi := min(lo+batch, n)
		q := fmt.Sprintf("INSERT INTO lake.lab.events SELECT i,CAST(i%%%d AS INTEGER),i%%10000+1 FROM range(%d,%d) t(i)", customers, lo, hi)
		t := time.Now()
		if _, err = db.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("append [%d,%d) has unresolved commit outcome; do not blindly retry: %w", lo, hi, err)
		}
		b, _ := json.Marshal(map[string]any{"first_id": lo, "end_id": hi, "seconds": time.Since(t).Seconds()})
		if _, err = journal.Write(append(b, '\n')); err != nil {
			return err
		}
		if err = journal.Sync(); err != nil {
			return err
		}
		fmt.Printf("acknowledged [%d,%d)\n", lo, hi)
	}
	return save("load", map[string]any{"rows": n, "customers": customers, "batch_rows": batch, "seconds": time.Since(start).Seconds()})
}
func verify() error {
	n, err := positive("ROWS", 2000000)
	if err != nil {
		return err
	}
	customers, err := positive("CUSTOMERS", 10000)
	if err != nil {
		return err
	}
	return verifyRows(n, customers)
}
func verifyRows(n, customers int64) error {
	var err error
	expected := (n/10000)*50005000 + (n%10000)*(n%10000+1)/2
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	db, err := duck(true)
	if err != nil {
		return err
	}
	defer db.Close()
	c, err := ch()
	if err != nil {
		return err
	}
	defer c.Close()
	if err = catalog(ctx, c); err != nil {
		return err
	}
	var dc, du, ds, dbad, dmin, dmax int64
	start := time.Now()
	q := fmt.Sprintf("SELECT count(*),count(DISTINCT event_id),sum(amount_cents),count(*) FILTER(WHERE event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR event_id<0 OR event_id>=%d OR customer_id!=event_id%%%d OR amount_cents!=event_id%%10000+1),min(event_id),max(event_id) FROM lake.lab.events", n, customers)
	if err = db.QueryRowContext(ctx, q).Scan(&dc, &du, &ds, &dbad, &dmin, &dmax); err != nil {
		return err
	}
	duckSeconds := time.Since(start).Seconds()
	var cc, cu, cbad uint64
	var cs, cmin, cmax int64
	start = time.Now()
	q = fmt.Sprintf("SELECT count(),uniqExact(event_id),sum(amount_cents),countIf(event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR event_id<0 OR event_id>=%d OR customer_id!=event_id%%%d OR amount_cents!=event_id%%10000+1),min(event_id),max(event_id) FROM lake.`lab.events` LIMIT 1", n, customers)
	if err = c.QueryRow(ctx, q).Scan(&cc, &cu, &cs, &cbad, &cmin, &cmax); err != nil {
		return err
	}
	chSeconds := time.Since(start).Seconds()
	if dc != n || du != n || ds != expected || dbad != 0 || dmin != 0 || dmax != n-1 || cc != uint64(n) || cu != uint64(n) || cs != expected || cbad != 0 || cmin != 0 || cmax != n-1 {
		return fmt.Errorf("oracle failed: DuckDB count/distinct/sum/bad/min/max=%d/%d/%d/%d/%d/%d; ClickHouse=%d/%d/%d/%d/%d/%d", dc, du, ds, dbad, dmin, dmax, cc, cu, cs, cbad, cmin, cmax)
	}
	var dv, cv string
	if err = db.QueryRowContext(ctx, "SELECT version()").Scan(&dv); err != nil {
		return err
	}
	if err = c.QueryRow(ctx, "SELECT version()").Scan(&cv); err != nil {
		return err
	}
	result := map[string]any{"rows": n, "unique_ids": n, "amount_cents": expected, "invalid_rows": 0, "missing_ids": 0, "duplicate_ids": 0, "duckdb": dv, "clickhouse": cv, "duckdb_check_seconds": duckSeconds, "clickhouse_check_seconds": chSeconds}
	fmt.Printf("ORACLE PASS: %d rows, %d cents, zero missing, duplicate or invalid IDs\n", n, expected)
	return save(env("REPORT_NAME", "verify"), result)
}
