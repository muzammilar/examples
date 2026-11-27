package main

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"strconv"
	"time"
)

type row struct {
	ID, Customer, Amount                                  int64
	Region                                                string
	OrderRevision, CustomerRevision, EventSecond, Version int64
}

func scan(rows *sql.Rows) (row, error) {
	var r row
	err := rows.Scan(&r.ID, &r.Customer, &r.Amount, &r.Region, &r.OrderRevision, &r.CustomerRevision, &r.EventSecond, &r.Version)
	return r, err
}

const columns = "order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,version"
const expectedSQL = "SELECT o.id,o.customer_id,o.amount_cents,c.region,o.revision,c.revision,o.event_second,o.revision*1000000+c.revision FROM orders o JOIN customers c ON o.customer_id=c.id WHERE NOT o.cancelled ORDER BY o.id"

func exact(ctx context.Context, pg, doris *sql.DB, table string) (int64, error) {
	if table != "checkpointed" && table != "asynchronous" {
		return 0, fmt.Errorf("invalid sink")
	}
	tx, err := pg.BeginTx(ctx, &sql.TxOptions{ReadOnly: true, Isolation: sql.LevelRepeatableRead})
	if err != nil {
		return 0, err
	}
	defer tx.Rollback()
	var want, got int64
	if err = tx.QueryRowContext(ctx, "SELECT count(*) FROM orders WHERE NOT cancelled").Scan(&want); err != nil {
		return 0, err
	}
	if err = doris.QueryRowContext(ctx, "SELECT count(*) FROM pipeline."+table).Scan(&got); err != nil {
		return 0, err
	}
	if got != want {
		return 0, fmt.Errorf("visibility pending: %s has %d, source snapshot %d", table, got, want)
	}
	expected, err := tx.QueryContext(ctx, expectedSQL)
	if err != nil {
		return 0, err
	}
	defer expected.Close()
	actual, err := doris.QueryContext(ctx, "SELECT "+columns+" FROM pipeline."+table+" ORDER BY order_id")
	if err != nil {
		return 0, err
	}
	defer actual.Close()
	var count int64
	for expected.Next() {
		e, err := scan(expected)
		if err != nil {
			return count, err
		}
		if !actual.Next() {
			return count, fmt.Errorf("missing sink row; driver error: %v", actual.Err())
		}
		a, err := scan(actual)
		if err != nil {
			return count, err
		}
		if a != e {
			return count, fmt.Errorf("body mismatch at source id %d: expected %+v actual %+v", e.ID, e, a)
		}
		count++
	}
	if err = expected.Err(); err != nil {
		return count, err
	}
	if actual.Next() {
		return count, fmt.Errorf("extra sink body")
	}
	if err = actual.Err(); err != nil {
		return count, err
	}
	if count != want {
		return count, fmt.Errorf("snapshot count differs")
	}
	return count, nil
}
func verify(ctx context.Context, pg, doris *sql.DB) error {
	for _, table := range []string{"checkpointed", "asynchronous"} {
		start := time.Now()
		end := start.Add(10 * time.Minute)
		for {
			attempt, cancel := context.WithTimeout(ctx, 90*time.Second)
			count, err := exact(attempt, pg, doris, table)
			cancel()
			if err == nil {
				emit(map[string]any{"event": "verified", "table": table, "rows": count, "wrong": 0, "duration_ms": float64(time.Since(start).Nanoseconds()) / 1e6})
				break
			}
			emit(map[string]any{"event": "not_converged", "table": table, "error": err.Error()})
			if time.Now().After(end) {
				return err
			}
			time.Sleep(time.Second)
		}
	}
	return receipts(ctx, pg)
}
func reader(ctx context.Context, doris *sql.DB, run string, n, customers int, finite bool) error {
	end := time.Now().Add(120 * time.Second)
	if !finite {
		end = time.Now().Add(240 * time.Second)
	}
	tables := []string{"checkpointed", "asynchronous"}
	if order := os.Getenv("READ_ORDER"); order == "async-first" {
		tables = []string{"asynchronous", "checkpointed"}
	} else if order != "" && order != "checkpoint-first" {
		return fmt.Errorf("invalid READ_ORDER")
	}
	iteration := 0
	for ; time.Now().Before(end) && (!finite || iteration < 110); iteration++ {
		if _, err := os.Stat("/results/stop-reader-" + actorToken(run)); err == nil {
			return nil
		} else if !os.IsNotExist(err) {
			return err
		}
		for _, table := range tables {
			started := time.Now()
			rows, err := doris.QueryContext(ctx, fmt.Sprintf("SELECT %s FROM pipeline.%s WHERE order_id>%d AND customer_id>%d ORDER BY order_id LIMIT 64", columns, table, n*9/10, customers/2))
			if err != nil {
				emit(map[string]any{"event": "read_error", "table": table, "error": err.Error()})
				continue
			}
			expectedRows := historicalRows(n, customers)
			count := 0
			for rows.Next() {
				r, err := scan(rows)
				if err != nil {
					rows.Close()
					return err
				}
				if count >= len(expectedRows) || r != expectedRows[count] {
					rows.Close()
					return fmt.Errorf("wrong successful historical body %+v", r)
				}
				count++
			}
			err = rows.Err()
			rows.Close()
			if err != nil {
				emit(map[string]any{"event": "read_error", "table": table, "error": err.Error()})
				continue
			}
			if count != 64 {
				return fmt.Errorf("historical read returned %d rows", count)
			}
			emit(map[string]any{"event": "read", "warmup": finite && iteration < 10, "table": table, "rows": count, "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6})
		}
		time.Sleep(200 * time.Millisecond)
	}
	if finite && iteration == 110 {
		return nil
	}
	return fmt.Errorf("reader supervision deadline reached without stop signal")
}

func historicalRows(n, customers int) []row {
	var result []row
	for id := int64(n*9/10 + 1); id <= int64(n) && len(result) < 64; id++ {
		customer := 1 + (id-1)%int64(customers)
		if customer <= int64(customers/2) {
			continue
		}
		result = append(result, row{ID: id, Customer: customer, Amount: 100 + id%1000, Region: "region-" + strconv.FormatInt(customer%16, 10), OrderRevision: 1, CustomerRevision: 1, EventSecond: 1700000000 + id%86400, Version: 1000001})
	}
	return result
}
