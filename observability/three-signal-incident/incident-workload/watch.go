package main

import (
	"bufio"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"time"
)

type mismatchError struct{ message string }
type projectionError struct{}

func (e *projectionError) Error() string {
	return "Numeric span/event attribute types changed to strings; exact fidelity failed, values/structure match only the explicit projection"
}

func (e *mismatchError) Error() string { return e.message }

type tail struct {
	file    *os.File
	reader  *bufio.Reader
	partial string
	latest  *RequestRecord
}

func (t *tail) poll(path string) error {
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
			var record RequestRecord
			if decodeErr := json.Unmarshal([]byte(t.partial), &record); decodeErr != nil {
				return decodeErr
			}
			t.partial = ""
			if record.Event == "response" && record.Outcome == "accepted" {
				saved := record
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

func metricPoint(ctx context.Context, backend string, f Fixture, id int) (int, error) {
	series, stamp, expected := f.metricValue(id)
	query := fmt.Sprintf(`checkout_latency_microseconds{run="%s",series="%d"}[1ms]`, f.Run, series)
	params := url.Values{"query": {query}, "time": {fmt.Sprintf("%d.%03d", stamp/1000, stamp%1000)}}
	if backend == "victoria" {
		params.Set("nocache", "1")
	}
	response, err := get(ctx, metricEndpoint(backend)+"?"+params.Encode(), "")
	if err != nil {
		return 0, err
	}
	defer response.Body.Close()
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
	if err = json.NewDecoder(io.LimitReader(response.Body, 1<<20)).Decode(&result); err != nil {
		return 0, err
	}
	if result.Status != "success" || result.Data.Type != "matrix" || result.Error != "" {
		return 0, fmt.Errorf("metric point query failed: %+v", result)
	}
	count := 0
	for _, row := range result.Data.Result {
		actual, err := checkMetricLabels(row.Metric, f)
		if err != nil || actual != series {
			return 0, &mismatchError{message: "metric point labels differ"}
		}
		for _, value := range row.Values {
			at, err := timestampMS(value[0])
			if err != nil || at != stamp {
				return 0, &mismatchError{message: "metric point timestamp differs"}
			}
			var text string
			if err = json.Unmarshal(value[1], &text); err != nil {
				return 0, err
			}
			number, err := strconv.ParseFloat(text, 64)
			if err != nil || number != expected {
				return 0, &mismatchError{message: "metric point value differs"}
			}
			count++
		}
	}
	return count, nil
}

func point(ctx context.Context, backend, signal string, f Fixture, id int) (int, error) {
	if signal == "metrics" {
		return metricPoint(ctx, backend, f, id)
	}
	if signal == "logs" {
		request := id / f.LogsPerRequest
		rows, err := logChunk(ctx, backend, f, request, request+1)
		if err != nil {
			return 0, err
		}
		count := 0
		for _, row := range rows {
			actual, err := checkLog(f, row, request, request+1)
			if err != nil {
				return 0, &mismatchError{message: err.Error()}
			}
			if actual == id {
				count++
			}
		}
		return count, nil
	}
	var rows []canonicalSpan
	var err error
	if backend == "greptime" {
		rows, err = greptimeTraceRange(ctx, f, id, id+1)
	} else {
		rows, err = nativeTrace(ctx, backend, f, id)
	}
	if err != nil {
		return 0, err
	}
	found := make(map[string]bool)
	projected := false
	for _, row := range rows {
		matched := false
		for role := 0; role < 3; role++ {
			if row.SpanID != hex.EncodeToString(f.spanID(id, role)) {
				continue
			}
			expected, err := expectedSpan(f, id, role)
			if err != nil {
				return 0, err
			}
			if !reflect.DeepEqual(row, expected) {
				if !reflect.DeepEqual(row, stringAttributeProjection(expected)) {
					return 0, &mismatchError{message: "trace point fields differ"}
				}
				projected = true
			}
			found[row.SpanID], matched = true, true
			break
		}
		if !matched {
			return 0, &mismatchError{message: "unexpected span in trace point"}
		}
	}
	if len(found) == 3 {
		if projected {
			return 1, &projectionError{}
		}
		return 1, nil
	}
	return 0, nil
}

// advanceAnchor keeps sampling the same acknowledged record through indexing delays.
// A diagnostic budget overrun is not evidence that a newer record is searchable.
func advanceAnchor(result string) bool {
	return result == "visible" || result == "fidelity-mismatch"
}

func watch(ctx context.Context, backend string, f, prior Fixture, directory string) error {
	if err := os.MkdirAll(filepath.Join(directory, "reader"), 0755); err != nil {
		return err
	}
	file, err := os.OpenFile(filepath.Join(directory, "reader/reads.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	tails := map[string]*tail{"metrics": {}, "logs": {}, "traces": {}}
	defer func() {
		for _, tail := range tails {
			if tail.file != nil {
				tail.file.Close()
			}
		}
	}()
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	anchors := make(map[string]*RequestRecord)
	sequence := 0
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
		if _, err = os.Stat(filepath.Join(directory, "stop")); err == nil {
			return nil
		} else if !os.IsNotExist(err) {
			return err
		}
		for _, signal := range []string{"metrics", "logs", "traces"} {
			tail := tails[signal]
			if err = tail.poll(filepath.Join(directory, signal, "requests.jsonl")); err != nil {
				return err
			}
			oldID := (sequence * 137) % prior.Requests
			if signal == "metrics" {
				oldID += (prior.MetricPoints - 1) * prior.Requests
			}
			if signal == "logs" {
				oldID *= prior.LogsPerRequest
			}
			for _, kind := range []string{"old", "recent"} {
				fixture, id := prior, oldID
				if kind == "recent" {
					if anchors[signal] == nil {
						anchors[signal] = tail.latest
					}
					if anchors[signal] == nil {
						continue
					}
					fixture, id = f, anchors[signal].Last-1
				}
				started := time.Now()
				copies, queryErr := point(ctx, backend, signal, fixture, id)
				result := "visible"
				if queryErr != nil {
					result = "rpc-fail"
					var wrong *mismatchError
					if errors.As(queryErr, &wrong) {
						result = "mismatch"
					}
					var projected *projectionError
					if errors.As(queryErr, &projected) {
						result = "fidelity-mismatch"
					}
				} else if copies == 0 {
					result = "pending"
					if kind == "old" {
						result = "mismatch"
						queryErr = &mismatchError{message: "previously verified old data disappeared"}
					}
				}
				row := map[string]any{"signal": signal, "kind": kind, "run": fixture.Run, "id": id, "started_at": started.UTC(), "ended_at": time.Now().UTC(), "query_ms": float64(time.Since(started).Nanoseconds()) / 1e6, "result": result, "copies_returned": copies}
				if kind == "recent" {
					row["ack_completed_at"] = anchors[signal].EndedAt
					row["sampled_ack_age_ms"] = float64(time.Since(anchors[signal].EndedAt).Nanoseconds()) / 1e6
				}
				if kind == "recent" && result == "pending" && time.Since(anchors[signal].EndedAt) > 10*time.Second {
					result = "visibility-budget-exceeded"
					row["result"] = result
				}
				if queryErr != nil {
					row["error"] = queryErr.Error()
				}
				if err = json.NewEncoder(file).Encode(row); err != nil {
					return err
				}
				if err = file.Sync(); err != nil {
					return err
				}
				if result == "mismatch" {
					return queryErr
				}
				if kind == "recent" && advanceAnchor(result) {
					anchors[signal] = nil
				}
			}
		}
		sequence++
	}
}
