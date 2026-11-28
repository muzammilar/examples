package main

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	logs "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	traces "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	"google.golang.org/protobuf/proto"
)

var httpClient = &http.Client{Timeout: 30 * time.Second, Transport: &http.Transport{Proxy: nil,
	DialContext:     (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
	MaxConnsPerHost: 4, MaxIdleConnsPerHost: 4, IdleConnTimeout: 90 * time.Second},
	CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirect refused") }}

var collectorHosts = map[string]string{"greptime": "greptime-collector:4318", "victoria": "victoria-collector:4318", "grafana": "grafana-collector:4318"}

type RequestRecord struct {
	Phase      string    `json:"phase"`
	Event      string    `json:"event"`
	Signal     string    `json:"signal"`
	First      int       `json:"first"`
	Last       int       `json:"last_exclusive"`
	StartedAt  time.Time `json:"started_at"`
	EndedAt    time.Time `json:"ended_at,omitempty"`
	Outcome    string    `json:"outcome"`
	Error      string    `json:"error,omitempty"`
	HTTPStatus int       `json:"http_status,omitempty"`
	RawBytes   int       `json:"raw_bytes"`
	WireBytes  int       `json:"wire_bytes"`
	RawSHA256  string    `json:"raw_sha256"`
	EncodingMS float64   `json:"encoding_ms"`
	HTTPMS     float64   `json:"http_ms,omitempty"`
}

type publication struct {
	EmittedItems           int     `json:"emitted_items"`
	PlannedItems           int     `json:"planned_items"`
	Signal                 string  `json:"signal"`
	Completed              bool    `json:"completed"`
	Requests               int     `json:"requests"`
	AcknowledgedRequests   int     `json:"acknowledged_requests"`
	UnacknowledgedRequests int     `json:"unacknowledged_requests"`
	ElapsedS               float64 `json:"elapsed_s"`
}

func appendRecord(file *os.File, row RequestRecord) error {
	if err := json.NewEncoder(file).Encode(row); err != nil {
		return err
	}
	return file.Sync()
}

func requestMessage(f Fixture, signal string, first, last int) proto.Message {
	switch signal {
	case "metrics":
		return f.metricRequest(first, last)
	case "logs":
		return f.logRequest(first, last)
	default:
		return f.traceRequest(first, last)
	}
}

func responseAccepted(signal string, body []byte) error {
	switch signal {
	case "metrics":
		var response metrics.ExportMetricsServiceResponse
		if err := proto.Unmarshal(body, &response); err != nil {
			return err
		}
		if p := response.PartialSuccess; p != nil && (p.RejectedDataPoints != 0 || p.ErrorMessage != "") {
			return fmt.Errorf("partial metrics response: %v", p)
		}
	case "logs":
		var response logs.ExportLogsServiceResponse
		if err := proto.Unmarshal(body, &response); err != nil {
			return err
		}
		if p := response.PartialSuccess; p != nil && (p.RejectedLogRecords != 0 || p.ErrorMessage != "") {
			return fmt.Errorf("partial logs response: %v", p)
		}
	case "traces":
		var response traces.ExportTraceServiceResponse
		if err := proto.Unmarshal(body, &response); err != nil {
			return err
		}
		if p := response.PartialSuccess; p != nil && (p.RejectedSpans != 0 || p.ErrorMessage != "") {
			return fmt.Errorf("partial traces response: %v", p)
		}
	default:
		return errors.New("invalid signal")
	}
	return nil
}

// An RPC failure is recorded and returned separately from a witness failure.
// Only a failed journal prevents the worker from continuing under an injected outage.
func send(ctx context.Context, file *os.File, address, signal string, f Fixture, first, last int) (bool, error) {
	began := time.Now()
	raw, err := proto.Marshal(requestMessage(f, signal, first, last))
	if err != nil {
		return false, err
	}
	var wire bytes.Buffer
	compressor := gzip.NewWriter(&wire)
	if _, err = compressor.Write(raw); err != nil {
		return false, err
	}
	if err = compressor.Close(); err != nil {
		return false, err
	}
	sum := sha256.Sum256(raw)
	phase, _ := os.ReadFile("/results/phase")
	row := RequestRecord{Phase: string(phase), Event: "intent", Signal: signal, First: first, Last: last, StartedAt: time.Now().UTC(), Outcome: "not-issued", RawBytes: len(raw), WireBytes: wire.Len(), RawSHA256: hex.EncodeToString(sum[:]), EncodingMS: float64(time.Since(began).Nanoseconds()) / 1e6}
	if err = appendRecord(file, row); err != nil {
		return false, err
	}
	request, err := http.NewRequestWithContext(ctx, "POST", address, bytes.NewReader(wire.Bytes()))
	if err != nil {
		return false, err
	}
	request.Header.Set("Content-Type", "application/x-protobuf")
	request.Header.Set("Content-Encoding", "gzip")
	issued := time.Now()
	response, rpcErr := httpClient.Do(request)
	if rpcErr == nil {
		row.HTTPStatus = response.StatusCode
		body, readErr := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
		closeErr := response.Body.Close()
		rpcErr = errors.Join(readErr, closeErr)
		if rpcErr == nil && len(body) > 1<<20 {
			rpcErr = errors.New("OTLP response exceeds 1 MiB")
		}
		if rpcErr == nil && response.StatusCode/100 != 2 {
			rpcErr = fmt.Errorf("HTTP %d: %s", response.StatusCode, string(body))
		}
		if rpcErr == nil {
			rpcErr = responseAccepted(signal, body)
		}
	}
	row.Event, row.Outcome, row.EndedAt = "response", "accepted", time.Now().UTC()
	row.HTTPMS = float64(time.Since(issued).Nanoseconds()) / 1e6
	if rpcErr != nil {
		row.Outcome, row.Error = "unacknowledged", rpcErr.Error()
	}
	if err = appendRecord(file, row); err != nil {
		return false, err
	}
	return rpcErr == nil, nil
}

func publishSignal(ctx context.Context, backend, signal string, f Fixture, directory string, pace time.Duration) (publication, error) {
	result := publication{Signal: signal}
	if err := os.MkdirAll(directory, 0755); err != nil {
		return result, err
	}
	file, err := os.OpenFile(filepath.Join(directory, "requests.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return result, err
	}
	defer file.Close()
	total, batch := f.Requests*f.MetricPoints, 1000
	if signal == "logs" {
		total = f.Requests * f.LogsPerRequest
	}
	if signal == "traces" {
		total, batch = f.Requests, 10
	}
	result.PlannedItems = total
	address := "http://" + collectorHosts[backend] + "/v1/" + signal
	began := time.Now()
	for first := 0; first < total; first += batch {
		if ctx.Err() != nil {
			return result, ctx.Err()
		}
		if _, err = os.Stat(filepath.Join(filepath.Dir(directory), "stop")); err == nil {
			break
		} else if !os.IsNotExist(err) {
			return result, err
		}
		accepted, err := send(ctx, file, address, signal, f, first, min(first+batch, total))
		if err != nil {
			return result, err
		}
		result.Requests++
		result.EmittedItems = min(first+batch, total)
		if accepted {
			result.AcknowledgedRequests++
		} else {
			result.UnacknowledgedRequests++
		}
		timer := time.NewTimer(pace)
		select {
		case <-ctx.Done():
			timer.Stop()
			return result, ctx.Err()
		case <-timer.C:
		}
	}
	result.Completed = result.EmittedItems == total
	result.ElapsedS = time.Since(began).Seconds()
	return result, nil
}

func publish(ctx context.Context, backend string, f Fixture, directory string, pace time.Duration) error {
	if _, ok := collectorHosts[backend]; !ok {
		return errors.New("unknown local collector")
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	type outcome struct {
		result publication
		err    error
	}
	finished := make(chan outcome, 3)
	var workers sync.WaitGroup
	for _, signal := range []string{"metrics", "logs", "traces"} {
		workers.Go(func() {
			r, err := publishSignal(ctx, backend, signal, f, filepath.Join(directory, signal), pace)
			if err != nil {
				cancel()
			}
			finished <- outcome{r, err}
		})
	}
	workers.Wait()
	close(finished)
	var combined error
	for row := range finished {
		if err := json.NewEncoder(os.Stdout).Encode(row.result); err != nil {
			combined = errors.Join(combined, err)
		}
		combined = errors.Join(combined, row.err)
	}
	return combined
}
