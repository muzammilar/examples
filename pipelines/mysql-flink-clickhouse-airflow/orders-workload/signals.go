package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"time"
)

const snapshotRequest = `{"type":"incremental","data-collections":["orders.orders"]}`

func requestBackfill(ctx context.Context, db *sql.DB) error {
	id, err := runID()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// A retry reuses this signal identity. A no-op duplicate produces no new row event.
	if _, err = tx.ExecContext(ctx, "INSERT INTO debezium_signal(id,type,data) VALUES (?,'execute-snapshot',?) ON DUPLICATE KEY UPDATE id=id", id, snapshotRequest); err != nil {
		return err
	}
	var kind, data string
	if err = tx.QueryRowContext(ctx, "SELECT type,data FROM debezium_signal WHERE id=?", id).Scan(&kind, &data); err != nil {
		return err
	}
	if kind != "execute-snapshot" || data != snapshotRequest {
		return errors.New("signal identity reused with different content")
	}
	if err = tx.Commit(); err != nil {
		return fmt.Errorf("signal commit ambiguous; retry must use the same RUN_ID: %w", err)
	}
	emit(map[string]any{"event": "backfill-requested", "signal": id, "data": data})
	return nil
}

func waitBackfill(ctx context.Context) error {
	id, err := runID()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 300*time.Second)
	defer cancel()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	for {
		r, e := query(ctx, "ch1", "SELECT JSONExtractString(payload,'type') AS type,JSONExtractString(payload,'additional_data','status') AS status,JSONExtractString(payload,'additional_data','total_rows_scanned') AS scanned FROM pipeline.history WHERE topic='cdc.notifications' AND JSONExtractString(payload,'id')='"+id+"' AND JSONExtractString(payload,'aggregate_type')='Incremental Snapshot' FORMAT JSONEachRow")
		if e != nil {
			return e
		}
		decoder := json.NewDecoder(r.Body)
		completed, table := false, false
		var notices []map[string]string
		for decoder.More() {
			var row map[string]string
			if e = decoder.Decode(&row); e != nil {
				break
			}
			notices = append(notices, row)
			completed = completed || row["type"] == "COMPLETED"
			table = table || (row["type"] == "TABLE_SCAN_COMPLETED" && row["status"] == "SUCCEEDED")
		}
		r.Body.Close()
		if e != nil {
			return e
		}
		if completed && table {
			if err = saveJSON("backfill-notifications.json", notices); err != nil {
				return err
			}
			emit(map[string]any{"event": "backfill-completed", "signal": id, "notifications": notices})
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}
