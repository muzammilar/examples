package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type wrongValue struct{ err error }

func (e *wrongValue) Error() string { return e.err.Error() }

type journalTail struct {
	file    *os.File
	reader  *bufio.Reader
	partial string
	latest  *RequestRecord
}

func (t *journalTail) poll(path string) error {
	if t.file == nil {
		file, err := os.Open(path)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		t.file, t.reader = file, bufio.NewReader(file)
	}
	for {
		line, err := t.reader.ReadString('\n')
		t.partial += line
		if strings.HasSuffix(t.partial, "\n") {
			var row RequestRecord
			if decode := json.Unmarshal([]byte(t.partial), &row); decode != nil {
				return decode
			}
			t.partial = ""
			if row.Event == "response" && row.Outcome == "accepted" {
				saved := row
				t.latest = &saved
			}
		}
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func openObservePoint(ctx context.Context, f Fixture, kind string, id int) (bool, error) {
	stream, predicate := "default", ""
	switch kind {
	case "metrics":
		series, stamp, _ := f.metricValue(id)
		stream = "checkout_latency_microseconds"
		predicate = fmt.Sprintf("series='%d' AND _timestamp=%d", series, stamp*1000)
	case "logs":
		predicate = fmt.Sprintf("log_id=%d", id)
	case "traces":
		predicate = fmt.Sprintf("request_id='%d'", id)
	default:
		return false, errors.New("unknown point signal")
	}
	rows, err := searchOpenObserve(ctx, f, kind, stream, predicate)
	if err != nil {
		return false, err
	}
	seen := make(map[int]bool)
	for _, row := range rows {
		actual := 0
		switch kind {
		case "metrics":
			actual, err = checkOpenObserveMetric(f, row)
		case "logs":
			actual, err = checkOpenObserveLog(f, row)
		case "traces":
			actual, err = checkOpenObserveTrace(f, row)
		}
		if err != nil {
			return false, &wrongValue{err}
		}
		logical := actual
		if kind == "traces" {
			logical /= 3
		}
		if logical != id || seen[actual] {
			return false, &wrongValue{fmt.Errorf("wrong/duplicate point identity: %d", actual)}
		}
		seen[actual] = true
	}
	expected := 1
	if kind == "traces" {
		expected = 3
	}
	return len(seen) == expected, nil
}

func watchOpenObserve(ctx context.Context, f, prior Fixture, directory string) error {
	if err := os.MkdirAll(filepath.Join(directory, "reader"), 0755); err != nil {
		return err
	}
	output, err := os.OpenFile(filepath.Join(directory, "reader/reads.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer output.Close()
	tails := map[string]*journalTail{"metrics": {}, "logs": {}, "traces": {}}
	defer func() {
		for _, tail := range tails {
			if tail.file != nil {
				tail.file.Close()
			}
		}
	}()
	anchors := make(map[string]*RequestRecord)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	sequence := 0
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
		if _, err := os.Stat(filepath.Join(directory, "stop")); err == nil {
			return nil
		} else if !os.IsNotExist(err) {
			return err
		}
		for _, kind := range []string{"metrics", "logs", "traces"} {
			tail := tails[kind]
			if err := tail.poll(filepath.Join(directory, kind, "requests.jsonl")); err != nil {
				return err
			}
			total := prior.Requests
			if kind == "logs" {
				total *= prior.LogsPerRequest
			}
			if kind == "metrics" {
				total *= prior.MetricPoints
			}
			for _, age := range []string{"old", "recent"} {
				fixture, id := prior, (sequence*137)%total
				if age == "recent" {
					if anchors[kind] == nil {
						anchors[kind] = tail.latest
					}
					if anchors[kind] == nil {
						continue
					}
					fixture, id = f, anchors[kind].Last-1
				}
				began := time.Now()
				visible, queryErr := openObservePoint(ctx, fixture, kind, id)
				result := "pending"
				if visible {
					result = "visible"
				}
				if age == "old" && !visible && queryErr == nil {
					result = "missing-old"
				}
				if queryErr != nil {
					result = "rpc-fail"
					var mismatch *wrongValue
					if errors.As(queryErr, &mismatch) {
						result = "mismatch"
					}
				}
				row := map[string]any{"sequence": sequence, "signal": kind, "kind": age, "run": fixture.Run, "id": id, "started_at": began.UTC(), "ended_at": time.Now().UTC(), "query_ms": float64(time.Since(began).Nanoseconds()) / 1e6, "result": result, "selected_projection": "OpenObserve native SQL logical fields; not lossless OTLP"}
				if age == "recent" {
					row["ack_completed_at"] = anchors[kind].EndedAt
					row["sampled_ack_age_ms"] = float64(time.Since(anchors[kind].EndedAt).Nanoseconds()) / 1e6
				}
				if queryErr != nil {
					row["error"] = queryErr.Error()
				}
				if err := json.NewEncoder(output).Encode(row); err != nil {
					return err
				}
				if err := output.Sync(); err != nil {
					return err
				}
				if result == "mismatch" {
					return queryErr
				}
				// Hold a pending ACK through native indexing delays instead of chasing newer data.
				if age == "recent" && visible {
					anchors[kind] = nil
				}
			}
		}
		sequence++
	}
}
