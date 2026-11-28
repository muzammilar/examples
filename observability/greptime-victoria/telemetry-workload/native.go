package main

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"time"

	"github.com/golang/snappy"
	"github.com/prometheus/prometheus/prompb"
)

//go:embed native-logs.yaml
var nativeLogPipeline []byte

func setupNativeLogs(ctx context.Context) error {
	address := "http://greptime:4000/v1/pipelines/lab_native_logs?db=public"
	request, err := http.NewRequestWithContext(ctx, "POST", address, bytes.NewReader(nativeLogPipeline))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", "application/x-yaml")
	response, err := httpClient.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return err
	}
	if response.StatusCode/100 != 2 {
		return fmt.Errorf("native pipeline setup: HTTP %d %s", response.StatusCode, body)
	}
	var result struct {
		Code  int    `json:"code"`
		Error string `json:"error"`
	}
	if err = json.Unmarshal(body, &result); err != nil {
		return err
	}
	if result.Code != 0 || result.Error != "" {
		return fmt.Errorf("native pipeline setup rejected: %s", body)
	}
	return nil
}

func nativeMetrics(f Fixture, first, last int) ([]byte, error) {
	series := make([]prompb.TimeSeries, 0, min(f.Series, last-first))
	positions := make(map[int]int)
	for i := first; i < last; i++ {
		id, stamp, value := f.metricValue(i)
		position, exists := positions[id]
		if !exists {
			position = len(series)
			positions[id] = position
			// Remote Write labels are sorted, including the metric name.
			series = append(series, prompb.TimeSeries{Labels: []prompb.Label{{Name: "__name__", Value: "fleet_native"}, {Name: "run", Value: f.Run}, {Name: "series", Value: strconv.Itoa(id)}, {Name: "tenant", Value: tenant(id)}}})
		}
		series[position].Samples = append(series[position].Samples, prompb.Sample{Timestamp: stamp, Value: value})
	}
	request := prompb.WriteRequest{Timeseries: series}
	return request.Marshal()
}

func nativeLogs(f Fixture, first, last int, backend string) ([]byte, error) {
	var out bytes.Buffer
	rows := make([]map[string]any, 0, last-first)
	for i := first; i < last; i++ {
		body, err := json.Marshal(f.logBody(i))
		if err != nil {
			return nil, err
		}
		row := map[string]any{"epoch_ms": f.StartMS + int64(i), "body": string(body), "run": f.Run, "tenant": tenant(i % f.Series)}
		if backend == "greptime" {
			rows = append(rows, row)
		} else {
			if err = json.NewEncoder(&out).Encode(row); err != nil {
				return nil, err
			}
		}
	}
	if backend == "greptime" {
		return json.Marshal(rows)
	}
	return out.Bytes(), nil
}

func sendNative(ctx context.Context, journal *Journal, backend, signal string, f Fixture, first, last int) error {
	began := time.Now()
	var raw, wire []byte
	var err error
	var address, contentType, encoding string
	if signal == "metrics" {
		raw, err = nativeMetrics(f, first, last)
		if err != nil {
			return err
		}
		wire = snappy.Encode(nil, raw)
		contentType, encoding = "application/x-protobuf", "snappy"
		address = "http://greptime:4000/v1/prometheus/write?db=public"
		if backend == "victoria" {
			address = "http://vmetrics:8428/api/v1/write"
		}
	} else {
		raw, err = nativeLogs(f, first, last, backend)
		if err != nil {
			return err
		}
		wire = raw
		contentType = "application/json"
		address = "http://greptime:4000/v1/ingest?db=public&table=native_logs&pipeline_name=lab_native_logs"
		if backend == "victoria" {
			address = "http://vlogs:9428/insert/jsonline?_stream_fields=run,tenant&_time_field=epoch_ms&_msg_field=body"
			contentType = "application/stream+json"
		}
	}
	accept := func(body []byte) error {
		if signal == "logs" && backend == "greptime" {
			var result struct {
				Code   int    `json:"code"`
				Error  string `json:"error"`
				Output []struct {
					Rows int `json:"affectedrows"`
				} `json:"output"`
			}
			if err := json.Unmarshal(body, &result); err != nil {
				return err
			}
			if result.Code != 0 || result.Error != "" || len(result.Output) != 1 || result.Output[0].Rows != last-first {
				return fmt.Errorf("native row admission differs: %s", body)
			}
			return nil
		}
		if len(bytes.TrimSpace(body)) != 0 {
			return errors.New("unexpected non-empty native success response: " + string(body))
		}
		return nil
	}
	return sendBytes(ctx, journal, address, signal, first, last, raw, wire, contentType, encoding, float64(time.Since(began).Nanoseconds())/1e6, accept)
}
