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
	"net/url"
	"os"
	"path/filepath"
	"time"

	logs "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	"google.golang.org/protobuf/proto"
)

var httpClient = &http.Client{
	Timeout:       30 * time.Second,
	CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("redirects disabled") },
	Transport: &http.Transport{
		Proxy:           nil,
		DialContext:     (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxConnsPerHost: 4, MaxIdleConnsPerHost: 4, IdleConnTimeout: 60 * time.Second,
		ResponseHeaderTimeout: 25 * time.Second,
	},
}

func localURL(address string) error {
	parsed, err := url.Parse(address)
	if err != nil {
		return err
	}
	if parsed.Scheme != "http" || parsed.User != nil {
		return errors.New("only owned HTTP endpoints are allowed")
	}
	switch parsed.Host {
	case "greptime:4000", "vmetrics:8428", "vlogs:9428":
		return nil
	default:
		return fmt.Errorf("unexpected endpoint %q", parsed.Host)
	}
}

// journal.Sync completes outside request timing. Unknown RPC outcomes are kept
// as ambiguous until the exact readback oracle reconciles their effects.
type Journal struct{ file *os.File }

func openJournal(directory string) (*Journal, error) {
	if err := os.MkdirAll(directory, 0755); err != nil {
		return nil, err
	}
	file, err := os.OpenFile(filepath.Join(directory, "requests.jsonl"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	return &Journal{file: file}, nil
}

func (j *Journal) append(value any) error {
	if err := json.NewEncoder(j.file).Encode(value); err != nil {
		return err
	}
	return j.file.Sync()
}

type RequestRecord struct {
	Event        string    `json:"event"`
	Signal       string    `json:"signal"`
	First        int       `json:"first"`
	Last         int       `json:"last_exclusive"`
	StartedAt    time.Time `json:"started_at"`
	EndedAt      time.Time `json:"ended_at"`
	EncodingMS   float64   `json:"encoding_ms"`
	HTTPMS       float64   `json:"http_ms"`
	RawBytes     int       `json:"raw_bytes"`
	WireBytes    int       `json:"wire_bytes"`
	BodySHA256   string    `json:"body_sha256"`
	ContentType  string    `json:"content_type"`
	WireEncoding string    `json:"wire_encoding"`
	Status       int       `json:"http_status"`
	Outcome      string    `json:"outcome"`
	Error        string    `json:"error,omitempty"`
}

func encodeRequest(message proto.Message, compressed bool) ([]byte, []byte, error) {
	raw, err := proto.MarshalOptions{Deterministic: true}.Marshal(message)
	if err != nil {
		return nil, nil, err
	}
	if !compressed {
		return raw, raw, nil
	}
	var target bytes.Buffer
	writer := gzip.NewWriter(&target)
	if _, err = writer.Write(raw); err != nil {
		return nil, nil, err
	}
	if err = writer.Close(); err != nil {
		return nil, nil, err
	}
	return raw, target.Bytes(), nil
}

func acceptResponse(signal string, body []byte) error {
	switch signal {
	case "metrics":
		var response metrics.ExportMetricsServiceResponse
		if err := proto.Unmarshal(body, &response); err != nil {
			return err
		}
		if partial := response.PartialSuccess; partial != nil && (partial.RejectedDataPoints != 0 || partial.ErrorMessage != "") {
			return fmt.Errorf("OTLP partial success: rejected=%d message=%s", partial.RejectedDataPoints, partial.ErrorMessage)
		}
	case "logs":
		var response logs.ExportLogsServiceResponse
		if err := proto.Unmarshal(body, &response); err != nil {
			return err
		}
		if partial := response.PartialSuccess; partial != nil && (partial.RejectedLogRecords != 0 || partial.ErrorMessage != "") {
			return fmt.Errorf("OTLP partial success: rejected=%d message=%s", partial.RejectedLogRecords, partial.ErrorMessage)
		}
	default:
		return errors.New("unknown signal")
	}
	return nil
}

func send(ctx context.Context, journal *Journal, address, signal string, first, last int, f Fixture, compressed bool) error {
	if err := localURL(address); err != nil {
		return err
	}
	encodedAt := time.Now()
	var message proto.Message
	if signal == "metrics" {
		message = f.metricRequest(first, last)
	} else {
		request, err := f.logRequest(first, last)
		if err != nil {
			return err
		}
		message = request
	}
	raw, wire, err := encodeRequest(message, compressed)
	if err != nil {
		return err
	}
	encoding := ""
	if compressed {
		encoding = "gzip"
	}
	return sendBytes(ctx, journal, address, signal, first, last, raw, wire, "application/x-protobuf", encoding, float64(time.Since(encodedAt).Nanoseconds())/1e6, func(body []byte) error { return acceptResponse(signal, body) })
}

func sendBytes(ctx context.Context, journal *Journal, address, signal string, first, last int, raw, wire []byte, contentType, encoding string, encodingMS float64, accept func([]byte) error) error {
	if err := localURL(address); err != nil {
		return err
	}
	sum := sha256.Sum256(raw)
	record := RequestRecord{Event: "intent", StartedAt: time.Now().UTC(), Signal: signal, First: first, Last: last, EncodingMS: encodingMS, RawBytes: len(raw), WireBytes: len(wire), BodySHA256: hex.EncodeToString(sum[:]), Outcome: "not-issued", ContentType: contentType, WireEncoding: encoding}
	if err := journal.append(record); err != nil {
		return err
	}
	request, err := http.NewRequestWithContext(ctx, "POST", address, bytes.NewReader(wire))
	if err != nil {
		return err
	}
	request.Header.Set("Content-Type", contentType)
	if encoding != "" {
		request.Header.Set("Content-Encoding", encoding)
	}
	if encoding == "snappy" {
		request.Header.Set("X-Prometheus-Remote-Write-Version", "0.1.0")
	}
	request.Header.Set("X-Greptime-DB-Name", "public")
	request.Header.Set("X-Greptime-Log-Extract-Keys", "run,tenant")
	request.Header.Set("VL-Stream-Fields", "run,tenant,service.name")
	record.Event = "response"
	issuedAt := time.Now().UTC()
	response, err := httpClient.Do(request)
	if err == nil {
		record.Status = response.StatusCode
		body, readErr := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
		closeErr := response.Body.Close()
		err = errors.Join(readErr, closeErr)
		if len(body) > 1<<20 {
			err = errors.Join(err, errors.New("response exceeds one MiB"))
		}
		if err == nil && response.StatusCode/100 != 2 {
			err = fmt.Errorf("HTTP %d: %s", response.StatusCode, body)
		}
		if err == nil {
			err = accept(body)
		}
	}
	record.EndedAt = time.Now().UTC()
	record.HTTPMS = float64(record.EndedAt.Sub(issuedAt).Nanoseconds()) / 1e6
	record.Outcome = "accepted"
	if err != nil {
		record.Outcome = "ambiguous-or-rejected"
		record.Error = err.Error()
	}
	if journalErr := journal.append(record); journalErr != nil {
		return errors.Join(err, journalErr)
	}
	return err
}
