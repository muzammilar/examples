package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net/http"
	"net/url"
	"strconv"
)

var readHosts = map[string]bool{"greptime-collector:8888": true, "victoria-collector:8888": true, "grafana-collector:8888": true, "greptime:4000": true, "vmetrics:8428": true, "vlogs:9428": true, "vtraces:10428": true, "prometheus:9090": true, "loki:3100": true, "tempo:3200": true,
	"greptime-collector:13133": true, "victoria-collector:13133": true, "grafana-collector:13133": true}

type httpError struct {
	Status int
	Body   string
}

func (e *httpError) Error() string { return fmt.Sprintf("HTTP %d: %s", e.Status, e.Body) }

func get(ctx context.Context, address, accept string) (*http.Response, error) {
	parsed, err := url.Parse(address)
	if err != nil || parsed.Scheme != "http" || parsed.User != nil || !readHosts[parsed.Host] {
		return nil, errors.New("read endpoint is outside this isolated lab")
	}
	request, err := http.NewRequestWithContext(ctx, "GET", address, nil)
	if err != nil {
		return nil, err
	}
	if accept != "" {
		request.Header.Set("Accept", accept)
	}
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode/100 != 2 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		response.Body.Close()
		return nil, &httpError{Status: response.StatusCode, Body: string(body)}
	}
	return response, nil
}

func timestampMS(raw json.RawMessage) (int64, error) {
	value, ok := new(big.Rat).SetString(string(raw))
	if !ok {
		return 0, errors.New("invalid timestamp")
	}
	value.Mul(value, big.NewRat(1000, 1))
	if !value.IsInt() || !value.Num().IsInt64() {
		return 0, errors.New("timestamp is not an exact millisecond")
	}
	return value.Num().Int64(), nil
}

func metricEndpoint(backend string) string {
	switch backend {
	case "greptime":
		return "http://greptime:4000/v1/prometheus/api/v1/query"
	case "victoria":
		return "http://vmetrics:8428/api/v1/query"
	case "grafana":
		return "http://prometheus:9090/api/v1/query"
	default:
		return ""
	}
}

func checkMetricLabels(labels map[string]string, f Fixture) (int, error) {
	id, err := strconv.Atoi(labels["series"])
	if err != nil || id < 0 || id >= f.Requests || labels["run"] != f.Run || labels["tenant"] != tenant(id) || labels["__name__"] != "checkout_latency_microseconds" {
		return 0, errors.New("metric business labels differ from fixture")
	}
	for key, value := range labels {
		expected := ""
		switch key {
		case "__name__", "run", "tenant", "series":
			continue
		case "scope.name", "otel_scope_name":
			expected = "checkout-incident-v1"
		case "scope.version", "otel_scope_version":
			expected = "1"
		case "job", "service.name", "service_name":
			expected = "checkout"
		case "deployment.environment.name", "deployment_environment_name":
			expected = "local-lab"
		default:
			return 0, fmt.Errorf("unverified metric metadata label %q=%q", key, value)
		}
		if value != expected {
			return 0, fmt.Errorf("wrong fixed metric metadata %s=%q", key, value)
		}
	}
	return id, nil
}

func metricReadback(ctx context.Context, backend string, f Fixture) ([]int, error) {
	seen := make([]int, f.Requests*f.MetricPoints)
	// Bounded point windows keep the native oracle below its 512 MiB budget.
	for first := 0; first < f.MetricPoints; first += 20 {
		last := min(first+20, f.MetricPoints)
		// Half-second bounds avoid depending on a backend's exact endpoint
		// inclusivity while still selecting the same integer-second samples.
		at := f.StartMS + int64(last-1)*1000 + 500
		query := fmt.Sprintf(`checkout_latency_microseconds{run="%s"}[%ds]`, f.Run, last-first)
		params := url.Values{"query": {query}, "time": {fmt.Sprintf("%d.%03d", at/1000, at%1000)}}
		if backend == "victoria" {
			params.Set("nocache", "1")
		}
		response, err := get(ctx, metricEndpoint(backend)+"?"+params.Encode(), "")
		if err != nil {
			return nil, err
		}
		var result struct {
			Status string `json:"status"`
			Error  string `json:"error"`
			Data   struct {
				Type   string `json:"resultType"`
				Result []struct {
					Metric map[string]string    `json:"metric"`
					Values [][2]json.RawMessage `json:"values"`
				} `json:"result"`
			} `json:"data"`
		}
		err = json.NewDecoder(io.LimitReader(response.Body, 64<<20)).Decode(&result)
		closeErr := response.Body.Close()
		if err = errors.Join(err, closeErr); err != nil {
			return nil, err
		}
		if result.Status != "success" || result.Error != "" || result.Data.Type != "matrix" {
			return nil, fmt.Errorf("invalid raw metric response status=%s type=%s error=%s", result.Status, result.Data.Type, result.Error)
		}
		for _, row := range result.Data.Result {
			series, err := checkMetricLabels(row.Metric, f)
			if err != nil {
				return nil, err
			}
			for _, sample := range row.Values {
				stamp, err := timestampMS(sample[0])
				if err != nil {
					return nil, err
				}
				delta := stamp - f.StartMS
				if delta < 0 || delta%1000 != 0 || delta/1000 < int64(first) || delta/1000 >= int64(last) {
					return nil, errors.New("metric sample timestamp outside requested window")
				}
				id := int(delta/1000)*f.Requests + series
				var text string
				if err = json.Unmarshal(sample[1], &text); err != nil {
					return nil, err
				}
				value, err := strconv.ParseFloat(text, 64)
				_, _, expected := f.metricValue(id)
				if err != nil || value != expected {
					return nil, fmt.Errorf("metric id=%d wrong value=%q expected=%v", id, text, expected)
				}
				seen[id]++
			}
		}
	}
	return seen, nil
}
