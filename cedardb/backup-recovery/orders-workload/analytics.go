package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
)

type aggregate struct {
	Bucket int64 `json:"bucket"`
	Count  int64 `json:"count"`
	Amount int64 `json:"amount"`
	Bytes  int64 `json:"payload_bytes"`
}

// Derive every expected group from the seed definition, independently of SQL.
func expectedGroups(count int64) []aggregate {
	result := make([]aggregate, 100)
	for i := range result {
		result[i].Bucket = int64(i)
	}
	for id := int64(0); id < count; id++ {
		group := &result[(id%10000)%100]
		group.Count++
		group.Amount += (id % 100000) * 17 % 100000
		group.Bytes += 256
	}
	return result
}

func checkGroups(actual, expected []aggregate) error {
	if len(actual) != len(expected) {
		return fmt.Errorf("group count: got %d want %d", len(actual), len(expected))
	}
	for i := range expected {
		if actual[i] != expected[i] {
			return fmt.Errorf("group %d: got %+v want %+v", i, actual[i], expected[i])
		}
	}
	return nil
}

func analyticQuery(ctx context.Context, conn *pgx.Conn, count int64, expected []aggregate) (time.Duration, time.Duration, error) {
	// Live appends use IDs >= 1e9. This predicate preserves the same query input
	// during both cells; independent readers still observe the changing live tail.
	query := fmt.Sprintf("SELECT customer %% 100 AS bucket, count(*), sum(amount), sum(length(payload)) FROM orders WHERE id < %d GROUP BY customer %% 100 ORDER BY bucket", count)
	started := time.Now()
	rows, err := conn.Query(ctx, query)
	if err != nil {
		return time.Since(started), 0, err
	}
	actual := make([]aggregate, 0, 100)
	for rows.Next() {
		var group aggregate
		if err := rows.Scan(&group.Bucket, &group.Count, &group.Amount, &group.Bytes); err != nil {
			rows.Close()
			return time.Since(started), 0, err
		}
		actual = append(actual, group)
	}
	rows.Close()
	if err := rows.Err(); err != nil {
		return time.Since(started), 0, err
	}
	fetched := time.Now()
	if err := checkGroups(actual, expected); err != nil {
		return fetched.Sub(started), time.Since(fetched), err
	}
	return fetched.Sub(started), time.Since(fetched), nil
}

func analytics(ctx context.Context, conn *pgx.Conn, count int64) error {
	warmup, err := number("ANALYTICS_WARMUP_S", 60, 0, 3600)
	if err != nil {
		return err
	}
	duration, err := number("ANALYTICS_DURATION_S", 300, 1, 3600)
	if err != nil {
		return err
	}
	expected := expectedGroups(count)
	out := json.NewEncoder(os.Stdout)
	for _, phase := range []struct {
		name    string
		seconds int64
	}{{"warmup", warmup}, {"measurement", duration}} {
		start := time.Now()
		if err := out.Encode(map[string]any{"kind": "phase-start", "phase": phase.name, "started_ns": start.UnixNano(), "duration_s": phase.seconds}); err != nil {
			return err
		}
		for time.Since(start) < time.Duration(phase.seconds)*time.Second {
			// Bound each query independently; a failed/cancelled query fails the cell.
			bounded, cancel := context.WithTimeout(ctx, 30*time.Second)
			queryStart := time.Now()
			elapsed, validation, queryErr := analyticQuery(bounded, conn, count, expected)
			cancel()
			record := map[string]any{"kind": "analytic-query", "phase": phase.name, "started_ns": queryStart.UnixNano(), "ended_ns": time.Now().UnixNano(), "query_fetch_ms": float64(elapsed) / 1e6, "validation_ms": float64(validation) / 1e6, "verified_groups": len(expected), "seed_rows": count, "passed": queryErr == nil}
			if queryErr != nil {
				record["error"] = queryErr.Error()
			}
			if err := out.Encode(record); err != nil {
				return err
			}
			if queryErr != nil {
				return queryErr
			}
		}
		finished := time.Now()
		if err := out.Encode(map[string]any{"kind": "phase-end", "phase": phase.name, "ended_ns": finished.UnixNano(), "elapsed_ns": finished.Sub(start).Nanoseconds()}); err != nil {
			return err
		}
	}
	return nil
}
