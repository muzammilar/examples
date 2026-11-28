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
	"strings"
	"time"
)

func get(ctx context.Context, address string) (*http.Response, error) {
	if err := localURL(address); err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, "GET", address, nil)
	if err != nil {
		return nil, err
	}
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode/100 != 2 {
		body, _ := io.ReadAll(io.LimitReader(response.Body, 4096))
		response.Body.Close()
		return nil, fmt.Errorf("HTTP %d: %s", response.StatusCode, body)
	}
	return response, nil
}

func health(ctx context.Context, backend string) error {
	var addresses []string
	switch backend {
	case "greptime":
		addresses = []string{"http://greptime:4000/health"}
	case "victoria":
		addresses = []string{"http://vmetrics:8428/health", "http://vlogs:9428/health"}
	default:
		return errors.New("invalid backend")
	}
	for _, address := range addresses {
		response, err := get(ctx, address)
		if err != nil {
			return err
		}
		_, err = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		closeErr := response.Body.Close()
		if err = errors.Join(err, closeErr); err != nil {
			return err
		}
	}
	return nil
}

func timestampMS(value json.RawMessage) (int64, error) {
	// Parse exact decimal seconds rather than rounding a float timestamp into a
	// valid sample. Sub-millisecond values fail this millisecond-only contract.
	rational, ok := new(big.Rat).SetString(string(value))
	if !ok {
		return 0, errors.New("invalid timestamp")
	}
	rational.Mul(rational, big.NewRat(1000, 1))
	if !rational.IsInt() || !rational.Num().IsInt64() {
		return 0, errors.New("timestamp not an exact millisecond")
	}
	return rational.Num().Int64(), nil
}

func verifyMetrics(ctx context.Context, backend string, f Fixture, metricName string) error {
	endpoint := "http://greptime:4000/v1/prometheus/api/v1/query"
	if backend == "victoria" {
		endpoint = "http://vmetrics:8428/api/v1/query"
	}
	query := fmt.Sprintf(`%s{run="%s"}[%ds]`, metricName, f.Run, f.Points+1)
	at := f.StartMS + int64(f.Points-1)*1000
	seconds := fmt.Sprintf("%d.%03d", at/1000, at%1000)
	response, err := get(ctx, endpoint+"?"+url.Values{"query": {query}, "time": {seconds}}.Encode())
	if err != nil {
		return err
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
	if err = json.NewDecoder(io.LimitReader(response.Body, 128<<20)).Decode(&result); err != nil {
		return err
	}
	if result.Status != "success" || result.Data.ResultType != "matrix" {
		return fmt.Errorf("unexpected raw metric response: status=%s type=%s error=%s", result.Status, result.Data.ResultType, result.Error)
	}
	seen := make([]bool, f.Series*f.Points)
	count := 0
	for _, series := range result.Data.Result {
		id, parseErr := strconv.Atoi(series.Metric["series"])
		if parseErr != nil || id < 0 || id >= f.Series || series.Metric["run"] != f.Run || series.Metric["tenant"] != tenant(id) || series.Metric["__name__"] != metricName {
			return errors.New("wrong series labels")
		}
		// Resource naming differs across OTLP implementations; these fixed
		// labels may represent the identical service, never business identity.
		for key, value := range series.Metric {
			switch key {
			case "__name__", "run", "tenant", "series":
			case "scope.version":
				if value != "unknown" {
					return errors.New("wrong instrumentation scope version")
				}
			case "scope.name":
				if value != "incident-corpus-v1" {
					return errors.New("wrong instrumentation scope")
				}
			case "job", "service.name", "service_name":
				if value != "checkout-lab" {
					return errors.New("wrong service identity")
				}
			default:
				return fmt.Errorf("unexpected metric label %q", key)
			}
		}
		for _, sample := range series.Values {
			stamp, e := timestampMS(sample[0])
			if e != nil {
				return e
			}
			delta := stamp - f.StartMS
			if delta < 0 || delta%1000 != 0 || delta/1000 >= int64(f.Points) {
				return errors.New("unexpected sample timestamp")
			}
			index := int(delta/1000)*f.Series + id
			var text string
			if e = json.Unmarshal(sample[1], &text); e != nil {
				return e
			}
			value, e := strconv.ParseFloat(text, 64)
			_, _, expected := f.metricValue(index)
			if e != nil || value != expected || seen[index] {
				return fmt.Errorf("wrong/duplicate metric index=%d value=%s", index, text)
			}
			seen[index] = true
			count++
		}
	}
	if count != len(seen) {
		return fmt.Errorf("missing metric samples: observed=%d expected=%d", count, len(seen))
	}
	return nil
}

func greptimeRows(ctx context.Context, query string) ([][]json.RawMessage, error) {
	address := "http://greptime:4000/v1/sql?" + url.Values{"sql": {query}}.Encode()
	response, err := get(ctx, address)
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
		return nil, fmt.Errorf("SQL response code=%d error=%s outputs=%d", result.Code, result.Error, len(result.Output))
	}
	return result.Output[0].Records.Rows, nil
}

func verifyLogBody(f Fixture, stamp int64, text string, first, last int, seen []bool) error {
	var body LogBody
	decoder := json.NewDecoder(strings.NewReader(text))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&body); err != nil {
		return err
	}
	if body.ID < first || body.ID >= last || stamp != f.StartMS+int64(body.ID) || seen[body.ID-first] || body != f.logBody(body.ID) {
		return fmt.Errorf("wrong/duplicate log body or timestamp: id=%d timestamp=%d", body.ID, stamp)
	}
	// Comparing the canonical full body also catches duplicate JSON keys and
	// trailing content which ordinary struct decoding would otherwise accept.
	canonical, err := json.Marshal(f.logBody(body.ID))
	if err != nil {
		return err
	}
	if text != string(canonical) {
		return errors.New("log body bytes changed")
	}
	seen[body.ID-first] = true
	return nil
}

func verifyLogs(ctx context.Context, backend string, f Fixture, protocol string) error {
	table, unitsPerMS := "opentelemetry_logs", int64(1000000)
	if protocol == "native" {
		table, unitsPerMS = "native_logs", 1
	}
	for first := 0; first < f.Logs; first += 10000 {
		last := min(first+10000, f.Logs)
		seen := make([]bool, last-first)
		start := time.UnixMilli(f.StartMS + int64(first)).UTC().Format(time.RFC3339Nano)
		end := time.UnixMilli(f.StartMS + int64(last)).UTC().Format(time.RFC3339Nano)
		if backend == "greptime" {
			rows, err := greptimeRows(ctx, fmt.Sprintf("SELECT CAST(timestamp AS BIGINT),body FROM %s WHERE run='%s' AND timestamp >= '%s' AND timestamp < '%s'", table, f.Run, start, end))
			if err != nil {
				return err
			}
			for _, row := range rows {
				if len(row) != 2 {
					return errors.New("unexpected log columns")
				}
				var nanos int64
				var body string
				if err = json.Unmarshal(row[0], &nanos); err != nil {
					return err
				}
				if err = json.Unmarshal(row[1], &body); err != nil {
					return err
				}
				if nanos%unitsPerMS != 0 {
					return errors.New("log timestamp lost millisecond contract")
				}
				if err = verifyLogBody(f, nanos/unitsPerMS, body, first, last, seen); err != nil {
					return err
				}
			}
		} else {
			query := fmt.Sprintf(`run:%q _time:[%s,%s)`, f.Run, start, end)
			response, err := get(ctx, "http://vlogs:9428/select/logsql/query?"+url.Values{"query": {query}}.Encode())
			if err != nil {
				return err
			}
			decoder := json.NewDecoder(io.LimitReader(response.Body, 32<<20))
			for decoder.More() {
				var row map[string]string
				if err = decoder.Decode(&row); err != nil {
					break
				}
				stamp, parseErr := time.Parse(time.RFC3339Nano, row["_time"])
				if parseErr != nil {
					err = parseErr
					break
				}
				if stamp.UnixNano()%1000000 != 0 {
					err = errors.New("log timestamp not millisecond aligned")
					break
				}
				if row["run"] != f.Run {
					err = errors.New("wrong run label")
					break
				}
				if err = verifyLogBody(f, stamp.UnixMilli(), row["_msg"], first, last, seen); err != nil {
					break
				}
			}
			closeErr := response.Body.Close()
			if err = errors.Join(err, closeErr); err != nil {
				return err
			}
		}
		for offset, ok := range seen {
			if !ok {
				return fmt.Errorf("missing log id=%d", first+offset)
			}
		}
	}
	return nil
}
