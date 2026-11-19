package main

import (
	"context"
	"fmt"
	"path/filepath"
	"strconv"
	"time"
)

// No application retries: an error after COMMIT may still have applied the batch.
func mutate(ctx context.Context, d *database, rows []Product) (host string, err error) {
	pool, host, err := d.primary(ctx)
	if err != nil {
		return host, err
	}
	tx, err := pool.Begin(ctx)
	if err != nil {
		return host, err
	}
	defer func() {
		cleanup, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		_ = tx.Rollback(cleanup)
	}()
	for _, row := range rows {
		id, parseErr := strconv.ParseInt(row.ID, 10, 64)
		if parseErr != nil {
			return host, parseErr
		}
		result, err := tx.Exec(ctx, "UPDATE inventory SET stock=$1,price_cents=$2,version=$3 WHERE id=$4::bigint AND tenant=$5 AND version<$3", row.Stock, row.PriceCents, row.Version, id, row.Tenant)
		if err != nil {
			return host, err
		}
		if result.RowsAffected() != 1 {
			return host, fmt.Errorf("identity/version guard rejected %s/%d", row.ID, row.Version)
		}
	}
	return host, tx.Commit(ctx)
}
func writeInventory(ctx context.Context, root string, f *Fixture, d *database, j *Journal, version, batch int) error {
	attempts, acks, ambiguous := 0, 0, 0
	for ctx.Err() == nil {
		stop, err := stopRequested(root, "writer")
		if err != nil {
			return err
		}
		if stop {
			break
		}
		if version >= 100000000 {
			return fmt.Errorf("version safety bound exceeded")
		}
		rows := make([]Product, batch)
		for i := range rows {
			row, err := f.product(f.Products[((version-1)*batch+i)%len(f.Products)].ID, version)
			if err != nil {
				return err
			}
			rows[i] = row
		}
		requestID := fmt.Sprint(version)
		if err := j.append(Event{Stage: "attempt", Rows: rows, RequestID: requestID}); err != nil {
			return err
		}
		started := time.Now()
		op, cancel := context.WithTimeout(ctx, 3*time.Second)
		host, err := mutate(op, d, rows)
		cancel()
		event := Event{Stage: "ack", Rows: rows, RequestID: requestID, Server: host, Seconds: time.Since(started).Seconds()}
		if err != nil {
			event.Stage = "ambiguous"
			event.Error = fmt.Sprintf("%.500s", err)
			ambiguous += len(rows)
		} else {
			acks += len(rows)
		}
		if err := j.append(event); err != nil {
			return err
		}
		attempts += len(rows)
		version++
		select {
		case <-ctx.Done():
		case <-time.After(100 * time.Millisecond):
		}
	}
	stop, err := stopRequested(root, "writer")
	if err != nil {
		return err
	}
	if !stop {
		return fmt.Errorf("writer exhausted duration before controller completed")
	}
	return saveJSON(filepath.Join(root, "writer.json"), map[string]any{"stopped_by_controller": stop, "attempted_rows": attempts, "acknowledged_rows": acks, "ambiguous_rows": ambiguous, "next_version": version, "mutation_retries": 0})
}
