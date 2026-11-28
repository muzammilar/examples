package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"reflect"
	"strconv"
	"time"
)

func queryExpected(f Fixture, name string) (map[string]float64, error) {
	expected := make(map[string]float64)
	switch name {
	case "metric-count", "metric-sum":
		for id := 0; id < f.Series*f.Points; id++ {
			series, _, value := f.metricValue(id)
			if name == "metric-count" {
				value = 1
			}
			expected[tenant(series)] += value
		}
	case "log-count":
		for id := 0; id < f.Logs; id++ {
			expected[tenant(id%f.Series)]++
		}
	default:
		return nil, errors.New("unknown query")
	}
	return expected, nil
}

func queryAnswer(ctx context.Context, backend string, f Fixture, name string) (map[string]float64, error) {
	answer := make(map[string]float64)
	if name == "log-count" {
		if backend == "greptime" {
			rows, err := greptimeRows(ctx, fmt.Sprintf("SELECT tenant,COUNT(*) FROM opentelemetry_logs WHERE run='%s' GROUP BY tenant", f.Run))
			if err != nil {
				return nil, err
			}
			for _, row := range rows {
				if len(row) != 2 {
					return nil, errors.New("unexpected grouped log columns")
				}
				var key string
				if err := json.Unmarshal(row[0], &key); err != nil {
					return nil, err
				}
				value, err := strconv.ParseFloat(string(row[1]), 64)
				if err != nil {
					return nil, err
				}
				if _, exists := answer[key]; exists {
					return nil, errors.New("duplicate log group")
				}
				answer[key] = value
			}
			return answer, nil
		}
		query := fmt.Sprintf(`run:%q | stats by (tenant) count() as total`, f.Run)
		response, err := get(ctx, "http://vlogs:9428/select/logsql/query?"+url.Values{"query": {query}}.Encode())
		if err != nil {
			return nil, err
		}
		defer response.Body.Close()
		decoder := json.NewDecoder(io.LimitReader(response.Body, 1<<20))
		for {
			var row map[string]string
			if err = decoder.Decode(&row); err == io.EOF {
				break
			} else if err != nil {
				return nil, err
			}
			if len(row) != 2 || row["tenant"] == "" {
				return nil, errors.New("unexpected log aggregate fields")
			}
			value, err := strconv.ParseFloat(row["total"], 64)
			if err != nil {
				return nil, err
			}
			if _, exists := answer[row["tenant"]]; exists {
				return nil, errors.New("duplicate log group")
			}
			answer[row["tenant"]] = value
		}
		return answer, nil
	}
	function := "count_over_time"
	if name == "metric-sum" {
		function = "sum_over_time"
	}
	query := fmt.Sprintf(`sum by (tenant) (%s(fleet_value{run="%s"}[%ds]))`, function, f.Run, f.Points+1)
	at := f.StartMS + int64(f.Points-1)*1000
	endpoint := "http://greptime:4000/v1/prometheus/api/v1/query"
	parameters := url.Values{"query": {query}, "time": {fmt.Sprintf("%d.%03d", at/1000, at%1000)}}
	if backend == "victoria" {
		endpoint = "http://vmetrics:8428/api/v1/query"
		parameters.Set("nocache", "1")
	}
	response, err := get(ctx, endpoint+"?"+parameters.Encode())
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var result struct {
		Status string `json:"status"`
		Error  string `json:"error"`
		Data   struct {
			Type   string `json:"resultType"`
			Result []struct {
				Metric map[string]string `json:"metric"`
				Value  []json.RawMessage `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return nil, err
	}
	if result.Status != "success" || result.Error != "" || result.Data.Type != "vector" {
		return nil, fmt.Errorf("invalid query response: %+v", result)
	}
	for _, row := range result.Data.Result {
		if len(row.Metric) != 1 || row.Metric["tenant"] == "" || len(row.Value) != 2 {
			return nil, errors.New("unexpected metric grouping/value")
		}
		returnedAt, err := timestampMS(row.Value[0])
		if err != nil || returnedAt != at {
			return nil, errors.New("aggregate evaluation timestamp differs from request")
		}
		var text string
		if err = json.Unmarshal(row.Value[1], &text); err != nil {
			return nil, err
		}
		value, err := strconv.ParseFloat(text, 64)
		if err != nil {
			return nil, err
		}
		key := row.Metric["tenant"]
		if _, exists := answer[key]; exists {
			return nil, errors.New("duplicate metric group")
		}
		answer[key] = value
	}
	return answer, nil
}

func runQueries(ctx context.Context, backend string, f Fixture, name, report string, repetitions int) error {
	expected, err := queryExpected(f, name)
	if err != nil {
		return err
	}
	rows := make([]map[string]any, 0, repetitions)
	var failure error
	for id := 0; id < repetitions; id++ {
		started := time.Now()
		answer, err := queryAnswer(ctx, backend, f, name)
		elapsed := time.Since(started)
		row := map[string]any{"iteration": id, "started_at": started.UTC(), "request_decode_ms": float64(elapsed.Nanoseconds()) / 1e6, "groups": len(answer), "correct": err == nil && reflect.DeepEqual(answer, expected)}
		if err != nil {
			row["error"] = err.Error()
			failure = err
		} else if !reflect.DeepEqual(answer, expected) {
			row["actual"] = answer
			row["expected"] = expected
			failure = errors.New("aggregate query differs from independent corpus oracle")
		}
		rows = append(rows, row)
		if failure != nil {
			break
		}
	}
	saved := saveExclusiveJSON(report, map[string]any{"backend": backend, "fixture": f, "query": name, "samples": rows, "first_query_scope": "Controller must restart this data role before invocation; physical OS cache remains retained. First request includes connection setup.", "warm_query_scope": "Identical query after the first; VictoriaMetrics nocache=1, Greptime/VictoriaLogs native cache defaults; no host-cache eviction.", "qualification": "Bounded query-latency diagnostic, not sustained capacity or established p99 tail."})
	return errors.Join(failure, saved)
}
