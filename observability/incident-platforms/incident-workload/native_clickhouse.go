package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
)

func nativeClickHouseQuery(ctx context.Context, backend, sql string) ([]nativeRow, error) {
	host, user, password := "", "", ""
	switch backend {
	case "clickstack":
		host, user, password = "clickstack-clickhouse:8123", "lab", "Local-clickstack-only"
	case "signoz":
		host, user, password = "signoz-clickhouse:8123", "default", "Local-Signoz-only"
	default:
		return nil, errors.New("unknown isolated native SQL backend")
	}
	request, err := http.NewRequestWithContext(ctx, "POST", "http://"+host+"/?max_execution_time=30&max_result_rows=10001&result_overflow_mode=throw&output_format_json_quote_64bit_integers=0&use_query_cache=0", strings.NewReader(sql+" FORMAT JSONEachRow"))
	if err != nil {
		return nil, err
	}
	request.SetBasicAuth(user, password)
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
	if err = errors.Join(readErr, response.Body.Close()); err != nil {
		return nil, err
	}
	if len(raw) > 16<<20 {
		return nil, errors.New("native SQL response exceeded 16 MiB")
	}
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("%s native SQL HTTP %d: %s", backend, response.StatusCode, raw)
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
