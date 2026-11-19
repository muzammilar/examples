package main

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"time"
)

func writeInventory(ctx context.Context, root string, f *Fixture, c *Client, j *Journal, version, batch int) error {
	attempted, acks, ambiguous, nativeRetries := 0, 0, 0, 0
	for ctx.Err() == nil {
		stop, err := stopRequested(root, "writer")
		if err != nil {
			return err
		}
		if stop {
			break
		}
		if version >= 100000000 {
			return fmt.Errorf("version bound exceeded")
		}
		rows := make([]Product, batch)
		for i := range rows {
			rows[i], err = f.product(f.Products[((version-1)*batch+i)%len(f.Products)].ID, version)
			if err != nil {
				return err
			}
		}
		if err = j.append(Event{Stage: "attempt", Rows: rows, RequestID: requestID(version)}); err != nil {
			return err
		}
		started := time.Now()
		op, cancel := context.WithTimeout(ctx, 3*time.Second)
		catalog, native, reply, err := c.mutate(op, rows)
		cancel()
		event := Event{Stage: "ack", Rows: rows, RequestID: requestID(version), Server: reply.Server, Attempts: reply.Attempts, Seconds: time.Since(started).Seconds(), CatalogVersion: catalog, NativeAttempts: native}
		if err != nil {
			event.Stage = "ambiguous"
			event.Error = fmt.Sprintf("%.500s", err)
			ambiguous += len(rows)
		} else {
			acks += len(rows)
			nativeRetries += max(0, native-1)
		}
		if writeErr := j.append(event); writeErr != nil {
			return writeErr
		}
		attempted += len(rows)
		if errors.Is(err, errProtocol) {
			return err
		}
		version++
		select {
		case <-ctx.Done():
		case <-time.After(100 * time.Millisecond):
		}
	}
	stopped, err := stopRequested(root, "writer")
	if err != nil {
		return err
	}
	if !stopped {
		return fmt.Errorf("writer exhausted duration")
	}
	return saveJSON(filepath.Join(root, "writer.json"), map[string]any{"stopped_by_controller": true, "attempted_rows": attempted, "acknowledged_rows": acks, "ambiguous_rows": ambiguous, "observed_native_commit_retries": nativeRetries, "application_mutation_retries": 0, "next_version": version})
}
