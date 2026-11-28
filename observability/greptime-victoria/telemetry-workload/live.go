package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sync/atomic"
	"time"
)

type acknowledgement struct {
	index     int
	completed time.Time
}

func liveReader(ctx context.Context, backend, signal string, f, prior Fixture, directory string, latest *atomic.Pointer[acknowledgement]) error {
	file, err := os.OpenFile(filepath.Join(directory, "reads.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	encoder := json.NewEncoder(file)
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var anchor *acknowledgement
	sequence := 0
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
		}
		oldID := (sequence * 137) % prior.Logs
		if signal == "metrics" {
			oldID = (prior.Points-1)*prior.Series + (sequence*137)%prior.Series
		}
		sequence++
		for _, kind := range []string{"old", "recent"} {
			fixture, id := prior, oldID
			if kind == "recent" {
				if anchor == nil {
					anchor = latest.Load()
				}
				if anchor == nil {
					continue
				}
				fixture, id = f, anchor.index
			}
			began := time.Now()
			phase := currentPhase()
			visible, queryErr := point(ctx, backend, signal, fixture, id)
			if ctx.Err() != nil {
				return nil
			}
			result := "visible"
			var evidence any
			fatal := false
			if queryErr != nil {
				result = "rpc-fail"
				var mismatch *mismatchError
				if errors.As(queryErr, &mismatch) {
					result, evidence, fatal = "mismatch", mismatch.evidence, true
				}
			} else if !visible {
				result = "pending"
				if kind == "old" {
					result, fatal = "mismatch", true
					queryErr = fmt.Errorf("previously verified historical id %d disappeared", id)
				}
				if kind == "recent" && time.Since(anchor.completed) > 5*time.Second {
					result = "visibility-budget-exceeded"
				}
			}
			row := map[string]any{"event": "read", "kind": kind, "signal": signal, "run": fixture.Run, "id": id, "result": result, "phase": phase, "phase_at_completion": currentPhase(), "started_at": began.UTC(), "ended_at": time.Now().UTC(), "duration_ms": float64(time.Since(began).Nanoseconds()) / 1e6}
			if kind == "recent" {
				row["ack_completed_at"] = anchor.completed.UTC()
				row["sampled_ack_age_ms"] = float64(time.Since(anchor.completed).Nanoseconds()) / 1e6
			}
			if queryErr != nil {
				row["error"] = queryErr.Error()
			}
			if evidence != nil {
				row["evidence"] = evidence
			}
			if err = encoder.Encode(row); err != nil {
				return err
			}
			if err = file.Sync(); err != nil {
				return err
			}
			if fatal {
				return queryErr
			}
			if kind == "recent" && (visible || result == "visibility-budget-exceeded") {
				anchor = nil
			}
		}
	}
}

func runLive(ctx context.Context, journal *Journal, backend, signal string, f, prior Fixture, batch int, compressed bool, output string, limit time.Duration, measured bool) error {
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	var latest atomic.Pointer[acknowledgement]
	readerDone := make(chan error, 1)
	go func() {
		err := liveReader(ctx, backend, signal, f, prior, output, &latest)
		if err != nil {
			cancel()
		}
		readerDone <- err
	}()
	total := f.Series * f.Points
	if signal == "logs" {
		total = f.Logs
	}
	address := "http://greptime:4000/v1/otlp/v1/" + signal
	if backend == "victoria" {
		address = "http://vmetrics:8428/opentelemetry/v1/metrics"
		if signal == "logs" {
			address = "http://vlogs:9428/insert/opentelemetry/v1/logs"
		}
	}
	requests, accepted, unacknowledged := 0, 0, 0
	var writeErr error
	began := time.Now()
	for first := 0; first < total; first += batch {
		if time.Since(began) >= limit {
			if !measured {
				writeErr = errors.New("live actor exceeded its five-minute bound")
			}
			break
		}
		if _, err := os.Stat(filepath.Join(output, "stop")); err == nil {
			break
		} else if !os.IsNotExist(err) {
			writeErr = err
			break
		}
		select {
		case err := <-readerDone:
			if err == nil {
				err = errors.New("reader stopped unexpectedly")
			}
			return err
		case <-ctx.Done():
			writeErr = ctx.Err()
		default:
		}
		if writeErr != nil {
			break
		}
		last := min(first+batch, total)
		err := send(ctx, journal, address, signal, first, last, f, compressed)
		requests++
		if err != nil {
			var completed *requestError
			if !errors.As(err, &completed) {
				writeErr = err
				break
			}
			unacknowledged++
		} else {
			accepted++
			latest.Store(&acknowledgement{index: last - 1, completed: time.Now()})
		}
		timer := time.NewTimer(200 * time.Millisecond)
		select {
		case <-ctx.Done():
			writeErr = ctx.Err()
		case err := <-readerDone:
			timer.Stop()
			if err == nil {
				err = errors.New("reader stopped unexpectedly")
			}
			return err
		case <-timer.C:
		}
		timer.Stop()
		if writeErr != nil {
			break
		}
	}
	if measured && time.Since(began) < limit && writeErr == nil {
		writeErr = errors.New("measured actor stopped or exhausted its fixture before its declared window completed")
	}
	cancel()
	readerErr := <-readerDone
	if err := errors.Join(writeErr, readerErr); err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "live-completed", "requests": requests, "accepted_requests": accepted, "unacknowledged_requests": unacknowledged, "elapsed_s": time.Since(began).Seconds(), "arrival_mode": "closed-loop, 200ms think time after each request; not a saturated throughput benchmark"})
}
