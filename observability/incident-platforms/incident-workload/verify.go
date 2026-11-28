package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func verifyNative(ctx context.Context, f Fixture, directory, report, backend, scope string, read func(context.Context, Fixture, string) ([]int, error)) error {
	var proof struct {
		Stopped   bool              `json:"actors_stopped"`
		Histories map[string]string `json:"histories"`
	}
	raw, err := os.ReadFile(filepath.Join(directory, "fenced.json"))
	if err != nil {
		return err
	}
	if err = json.Unmarshal(raw, &proof); err != nil {
		return err
	}
	if !proof.Stopped {
		return errors.New("publication is not fenced")
	}
	results := make(map[string]any)
	var combined error
	for _, signal := range []string{"metrics", "logs", "traces"} {
		total := f.Requests
		if signal == "metrics" {
			total *= f.MetricPoints
		}
		if signal == "logs" {
			total *= f.LogsPerRequest
		}
		history, err := readHistory(filepath.Join(directory, signal, "requests.jsonl"), signal, total)
		if err != nil {
			return err
		}
		if history.digest != proof.Histories[signal] {
			return errors.New("frozen journal changed")
		}
		started := time.Now()
		seen, err := read(ctx, f, signal)
		var counts reconciliation
		if err == nil {
			counts, err = history.reconcile(seen)
		}
		entry := map[string]any{"complete": err == nil, "counts": counts, "elapsed_s": time.Since(started).Seconds(), "history_sha256": history.digest}
		if err == nil && (len(counts.MissingAcknowledged) > 0 || len(counts.Unexpected) > 0 || counts.DuplicateCopies > 0) {
			err = errors.New("missing ACKs, unexpected logical IDs or duplicate returned copies")
		}
		if err != nil {
			entry["error"] = err.Error()
			combined = errors.Join(combined, fmt.Errorf("%s: %w", signal, err))
		}
		results[signal] = entry
	}
	file, err := os.OpenFile(report, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return errors.Join(combined, err)
	}
	written := json.NewEncoder(file).Encode(map[string]any{"backend": backend, "fixture": f, "signals": results, "common_projection_passed": combined == nil, "lossless_otlp_roundtrip": false, "scope": scope})
	return errors.Join(combined, written, file.Sync(), file.Close())
}
