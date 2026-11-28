package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strconv"
	"time"
)

type mismatchError struct {
	message  string
	evidence any
}

func (e *mismatchError) Error() string { return e.message }

func metricPoint(ctx context.Context, backend string, f Fixture, index int) (bool, error) {
	series, stamp, expected := f.metricValue(index)
	endpoint := "http://greptime:4000/v1/prometheus/api/v1/query"
	if backend == "victoria" {
		endpoint = "http://vmetrics:8428/api/v1/query"
	}
	parameters := url.Values{"query": {fmt.Sprintf(`fleet_value{run=%q,series=%q}[1ms]`, f.Run, strconv.Itoa(series))}, "time": {fmt.Sprintf("%d.%03d", stamp/1000, stamp%1000)}}
	if backend == "victoria" {
		parameters.Set("nocache", "1")
	}
	response, err := get(ctx, endpoint+"?"+parameters.Encode())
	if err != nil {
		return false, err
	}
	defer response.Body.Close()
	var result struct {
		Status string `json:"status"`
		Error  string `json:"error"`
		Data   struct {
			ResultType string `json:"resultType"`
			Result     []struct {
				Metric map[string]string    `json:"metric"`
				Values [][2]json.RawMessage `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return false, err
	}
	if result.Status != "success" || result.Data.ResultType != "matrix" {
		return false, fmt.Errorf("point query failed: %s %s", result.Status, result.Error)
	}
	if len(result.Data.Result) == 0 {
		return false, nil
	}
	if len(result.Data.Result) != 1 || len(result.Data.Result[0].Values) != 1 {
		return false, &mismatchError{message: "non-unique metric point", evidence: result.Data}
	}
	actual := result.Data.Result[0]
	var text string
	if err = json.Unmarshal(actual.Values[0][1], &text); err != nil {
		return false, err
	}
	value, err := strconv.ParseFloat(text, 64)
	if err != nil {
		return false, err
	}
	observedTime, err := timestampMS(actual.Values[0][0])
	if err != nil {
		return false, err
	}
	if value != expected || observedTime != stamp || actual.Metric["run"] != f.Run || actual.Metric["series"] != strconv.Itoa(series) || actual.Metric["tenant"] != tenant(series) {
		return false, &mismatchError{message: "metric point differs from source", evidence: map[string]any{"actual": actual, "expected_value": expected, "expected_timestamp_ms": stamp}}
	}
	return true, nil
}

func logPoint(ctx context.Context, backend string, f Fixture, id int) (bool, error) {
	start := time.UnixMilli(f.StartMS + int64(id)).UTC().Format(time.RFC3339Nano)
	end := time.UnixMilli(f.StartMS + int64(id) + 1).UTC().Format(time.RFC3339Nano)
	var stamp int64
	var body, indexedTenant string
	if backend == "greptime" {
		rows, err := greptimeRows(ctx, fmt.Sprintf("SELECT CAST(timestamp AS BIGINT),body,tenant FROM opentelemetry_logs WHERE run='%s' AND timestamp>='%s' AND timestamp<'%s'", f.Run, start, end))
		if err != nil {
			return false, err
		}
		if len(rows) == 0 {
			return false, nil
		}
		if len(rows) != 1 || len(rows[0]) != 3 {
			return false, &mismatchError{message: "non-unique log point", evidence: rows}
		}
		if err = json.Unmarshal(rows[0][0], &stamp); err != nil {
			return false, err
		}
		if stamp%1000000 != 0 {
			return false, &mismatchError{message: "wrong log timestamp precision", evidence: rows}
		}
		stamp /= 1000000
		if err = json.Unmarshal(rows[0][1], &body); err != nil {
			return false, err
		}
		if err = json.Unmarshal(rows[0][2], &indexedTenant); err != nil {
			return false, err
		}
	} else {
		query := fmt.Sprintf(`run:%q _time:[%s,%s)`, f.Run, start, end)
		response, err := get(ctx, "http://vlogs:9428/select/logsql/query?"+url.Values{"query": {query}}.Encode())
		if err != nil {
			return false, err
		}
		defer response.Body.Close()
		decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
		var row map[string]string
		if err = decoder.Decode(&row); err == io.EOF {
			return false, nil
		} else if err != nil {
			return false, err
		}
		var duplicate any
		if err = decoder.Decode(&duplicate); err != io.EOF {
			return false, &mismatchError{message: "non-unique or malformed log point", evidence: duplicate}
		}
		parsed, err := time.Parse(time.RFC3339Nano, row["_time"])
		if err != nil {
			return false, err
		}
		if parsed.UnixNano()%1000000 != 0 || row["run"] != f.Run {
			return false, &mismatchError{message: "wrong log metadata", evidence: row}
		}
		stamp, body, indexedTenant = parsed.UnixMilli(), row["_msg"], row["tenant"]
	}
	if err := verifyLogBody(f, stamp, body, indexedTenant, id, id+1, make([]bool, 1)); err != nil {
		return false, &mismatchError{message: err.Error(), evidence: map[string]any{"timestamp_ms": stamp, "body": body, "indexed_tenant": indexedTenant, "expected": f.logBody(id)}}
	}
	return true, nil
}

func point(ctx context.Context, backend, signal string, f Fixture, id int) (bool, error) {
	switch signal {
	case "metrics":
		return metricPoint(ctx, backend, f, id)
	case "logs":
		return logPoint(ctx, backend, f, id)
	default:
		return false, errors.New("invalid signal")
	}
}
