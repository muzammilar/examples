package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
)

func inspectSigNoz(ctx context.Context) error {
	rows, err := nativeClickHouseQuery(ctx, "signoz", "SELECT database,table,name,type FROM system.columns WHERE database IN ('signoz_logs','signoz_traces','signoz_metrics') ORDER BY database,table,position")
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"native_schema": rows, "meaning": "Native schema only, no workload qualification"})
}

func sampleSigNoz(ctx context.Context, f Fixture) error {
	queries := []struct{ name, sql string }{
		{"logs_v2", fmt.Sprintf("SELECT * FROM signoz_logs.logs_v2 WHERE attributes_string['run']='%s' LIMIT 1", f.Run)},
		{"signoz_index_v3", fmt.Sprintf("SELECT * FROM signoz_traces.signoz_index_v3 WHERE attributes_string['run']='%s' LIMIT 1", f.Run)},
		{"time_series_v4", fmt.Sprintf("SELECT * FROM signoz_metrics.time_series_v4 WHERE attrs['run']='%s' LIMIT 1", f.Run)},
		{"samples_v4", fmt.Sprintf("SELECT * FROM signoz_metrics.samples_v4 WHERE metric_name='checkout_latency_microseconds' AND fingerprint IN (SELECT fingerprint FROM signoz_metrics.time_series_v4 WHERE attrs['run']='%s') LIMIT 1", f.Run)},
	}
	for _, query := range queries {
		rows, err := nativeClickHouseQuery(ctx, "signoz", query.sql)
		if err != nil {
			return err
		}
		if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"table": query.name, "rows": rows, "meaning": "Bounded sample only, not full-value correctness"}); err != nil {
			return err
		}
	}
	return nil
}
