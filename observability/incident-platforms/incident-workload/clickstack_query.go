package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

// Only the example-owned native ClickHouse endpoint; no endpoint discovery.
func clickStackQuery(ctx context.Context, sql string) ([]nativeRow, error) {
	request, err := http.NewRequestWithContext(ctx, "POST", "http://clickstack-clickhouse:8123/?max_execution_time=30&max_result_rows=10001&result_overflow_mode=throw&output_format_json_quote_64bit_integers=0&use_query_cache=0", strings.NewReader(sql+" FORMAT JSONEachRow"))
	if err != nil {
		return nil, err
	}
	request.SetBasicAuth("lab", "Local-clickstack-only")
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
	if err = errors.Join(readErr, response.Body.Close()); err != nil {
		return nil, err
	}
	if len(raw) > 16<<20 {
		return nil, errors.New("native ClickStack query exceeded 16 MiB")
	}
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("native ClickStack HTTP %d: %s", response.StatusCode, raw)
	}
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	rows := []nativeRow{}
	for {
		var row nativeRow
		if err = decoder.Decode(&row); errors.Is(err, io.EOF) {
			return rows, nil
		}
		if err != nil {
			return nil, err
		}
		rows = append(rows, row)
	}
}

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
