package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
)

// Only the example-owned native ClickHouse endpoint; no endpoint discovery.
func inspectClickStack(ctx context.Context) error {
	rows, err := clickStackQuery(ctx, "SELECT table,name,type FROM system.columns WHERE database='default' ORDER BY table,position")
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"native_schema": rows, "meaning": "Schema inspection, not input correctness"})
}

func sampleClickStack(ctx context.Context, f Fixture) error {
	for _, query := range []struct{ table, attributes string }{{"otel_logs", "LogAttributes"}, {"otel_traces", "SpanAttributes"}, {"otel_metrics_gauge", "Attributes"}} {
		rows, err := clickStackQuery(ctx, fmt.Sprintf("SELECT * FROM %s WHERE %s['run']='%s' LIMIT 1", query.table, query.attributes, f.Run))
		if err != nil {
			return err
		}
		if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"table": query.table, "rows": rows, "meaning": "Bounded native sample, not a full correctness verdict"}); err != nil {
			return err
		}
	}
	return nil
}

func clickStackQuery(ctx context.Context, sql string) ([]nativeRow, error) {
	return nativeClickHouseQuery(ctx, "clickstack", sql)
}
