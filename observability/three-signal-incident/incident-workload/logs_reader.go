package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"strconv"
	"strings"
	"time"
)

func greptimeRows(ctx context.Context, query string) ([][]json.RawMessage, error) {
	response, err := get(ctx, "http://greptime:4000/v1/sql?"+url.Values{"db": {"public"}, "sql": {query}}.Encode(), "")
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	var result struct {
		Code   int    `json:"code"`
		Error  string `json:"error"`
		Output []struct {
			Records struct {
				Rows [][]json.RawMessage `json:"rows"`
			} `json:"records"`
		} `json:"output"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 32<<20)).Decode(&result); err != nil {
		return nil, err
	}
	if result.Code != 0 || result.Error != "" || len(result.Output) != 1 {
		return nil, fmt.Errorf("SQL result code=%d error=%s outputs=%d", result.Code, result.Error, len(result.Output))
	}
	return result.Output[0].Records.Rows, nil
}

var lokiBodyFilter bool

type nativeLog struct {
	stamp  uint64
	body   string
	fields map[string]string
}

func checkLog(f Fixture, row nativeLog, firstRequest, lastRequest int) (int, error) {
	var body LogBody
	decoder := json.NewDecoder(strings.NewReader(row.body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		return 0, err
	}
	if body.ID < firstRequest*f.LogsPerRequest || body.ID >= lastRequest*f.LogsPerRequest {
		return 0, fmt.Errorf("log id=%d outside requested range", body.ID)
	}
	expected := f.body(body.ID)
	canonical, err := json.Marshal(expected)
	if err != nil {
		return 0, err
	}
	if !bytes.Equal(canonical, []byte(row.body)) || body != expected || row.stamp != f.logStamp(body.ID) {
		return 0, fmt.Errorf("wrong log body/time id=%d", body.ID)
	}
	role, level, number := 0, "INFO", "9"
	if incident(expected.Request) {
		role, level, number = 2, "ERROR", "17"
	}
	fields := map[string]string{"tenant": expected.Tenant, "trace_id": expected.TraceID, "span_id": hex.EncodeToString(f.spanID(expected.Request, role)), "severity_text": level, "severity_number": number}
	for key, value := range fields {
		if row.fields[key] != value {
			return 0, fmt.Errorf("wrong native log %s=%q expected=%q id=%d", key, row.fields[key], value, body.ID)
		}
	}
	return body.ID, nil
}

func logChunk(ctx context.Context, backend string, f Fixture, first, last int) ([]nativeLog, error) {
	startMS, endMS := f.StartMS+int64(first)*10, f.StartMS+int64(last)*10
	start, end := time.UnixMilli(startMS).UTC().Format(time.RFC3339Nano), time.UnixMilli(endMS).UTC().Format(time.RFC3339Nano)
	if backend == "greptime" {
		rows, err := greptimeRows(ctx, fmt.Sprintf("SELECT CAST(timestamp AS BIGINT),body,tenant,trace_id,span_id,severity_text,severity_number FROM opentelemetry_logs WHERE run='%s' AND timestamp>='%s' AND timestamp<'%s'", f.Run, start, end))
		if err != nil {
			return nil, err
		}
		logs := make([]nativeLog, 0, len(rows))
		for _, row := range rows {
			if len(row) != 7 {
				return nil, errors.New("unexpected log SQL columns")
			}
			var item nativeLog
			if err = json.Unmarshal(row[0], &item.stamp); err != nil {
				return nil, err
			}
			if err = json.Unmarshal(row[1], &item.body); err != nil {
				return nil, err
			}
			item.fields = make(map[string]string)
			for index, key := range []string{"tenant", "trace_id", "span_id", "severity_text"} {
				var value string
				if err = json.Unmarshal(row[index+2], &value); err != nil {
					return nil, err
				}
				item.fields[key] = value
			}
			var number int64
			if err = json.Unmarshal(row[6], &number); err != nil {
				return nil, err
			}
			item.fields["severity_number"] = strconv.FormatInt(number, 10)
			logs = append(logs, item)
		}
		return logs, nil
	}
	if backend == "victoria" {
		query := fmt.Sprintf(`run:%q _time:[%s,%s)`, f.Run, start, end)
		response, err := get(ctx, "http://vlogs:9428/select/logsql/query?"+url.Values{"query": {query}, "limit": {"5000"}}.Encode(), "")
		if err != nil {
			return nil, err
		}
		defer response.Body.Close()
		decoder := json.NewDecoder(io.LimitReader(response.Body, 32<<20))
		var logs []nativeLog
		for {
			var row map[string]string
			if err = decoder.Decode(&row); err == io.EOF {
				break
			} else if err != nil {
				return nil, err
			}
			stamp, err := time.Parse(time.RFC3339Nano, row["_time"])
			if err != nil || stamp.UnixNano() < 0 {
				return nil, errors.New("invalid native log timestamp")
			}
			logs = append(logs, nativeLog{stamp: uint64(stamp.UnixNano()), body: row["_msg"], fields: row})
		}
		return logs, nil
	}
	query := fmt.Sprintf(`{service_name="checkout"} | run=%q`, f.Run)
	if lokiBodyFilter {
		if last-first != 1 {
			return nil, errors.New("body probe must cover exactly one request")
		}
		query = fmt.Sprintf(`{service_name="checkout"} |= %q`, hex.EncodeToString(f.traceID(first)))
	}
	parameters := url.Values{"query": {query}, "start": {strconv.FormatInt(startMS*1000000, 10)}, "end": {strconv.FormatInt(endMS*1000000-1, 10)}, "direction": {"forward"}, "limit": {"5000"}}
	response, err := get(ctx, "http://loki:3100/loki/api/v1/query_range?"+parameters.Encode(), "")
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
				Stream map[string]string   `json:"stream"`
				Values [][]json.RawMessage `json:"values"`
			} `json:"result"`
		} `json:"data"`
	}
	if err = json.NewDecoder(io.LimitReader(response.Body, 32<<20)).Decode(&result); err != nil {
		return nil, err
	}
	if result.Status != "success" || result.Error != "" || result.Data.Type != "streams" {
		return nil, fmt.Errorf("invalid Loki result status=%s type=%s error=%s", result.Status, result.Data.Type, result.Error)
	}
	var logs []nativeLog
	for _, stream := range result.Data.Result {
		for _, row := range stream.Values {
			if len(row) < 2 || len(row) > 3 {
				return nil, errors.New("invalid Loki value tuple")
			}
			var timestamp, body string
			if err = json.Unmarshal(row[0], &timestamp); err != nil {
				return nil, err
			}
			if err = json.Unmarshal(row[1], &body); err != nil {
				return nil, err
			}
			stamp, err := strconv.ParseUint(timestamp, 10, 64)
			if err != nil {
				return nil, err
			}
			fields := make(map[string]string, len(stream.Stream))
			for key, value := range stream.Stream {
				fields[key] = value
			}
			if len(row) == 3 {
				var metadata map[string]string
				if err = json.Unmarshal(row[2], &metadata); err != nil {
					return nil, err
				}
				for key, value := range metadata {
					fields[key] = value
				}
			}
			logs = append(logs, nativeLog{stamp: stamp, body: body, fields: fields})
		}
	}
	return logs, nil
}

func logReadback(ctx context.Context, backend string, f Fixture) ([]int, error) {
	seen := make([]int, f.Requests*f.LogsPerRequest)
	var readRange func(int, int) error
	readRange = func(first, last int) error {
		rows, err := logChunk(ctx, backend, f, first, last)
		if err != nil {
			return err
		}
		if backend != "greptime" && len(rows) >= 5000 {
			if last-first == 1 {
				return errors.New("one request's physical copies exceed native query cap; completeness unproven")
			}
			middle := first + (last-first)/2
			if err = readRange(first, middle); err != nil {
				return err
			}
			return readRange(middle, last)
		}
		for _, row := range rows {
			id, err := checkLog(f, row, first, last)
			if err != nil {
				return err
			}
			seen[id]++
		}
		return nil
	}
	width := max(1, 4000/f.LogsPerRequest)
	for first := 0; first < f.Requests; first += width {
		if err := readRange(first, min(first+width, f.Requests)); err != nil {
			return nil, err
		}
	}
	return seen, nil
}
