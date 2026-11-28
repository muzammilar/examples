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

func verify(ctx context.Context, backend string, f Fixture, directory, report string, allowProjection bool) error {
	raw, err := os.ReadFile(filepath.Join(directory, "fenced.json"))
	if err != nil {
		return err
	}
	var proof struct {
		Histories     map[string]string `json:"histories"`
		ActorsStopped bool              `json:"actors_stopped"`
	}
	if err = json.Unmarshal(raw, &proof); err != nil {
		return err
	}
	if !proof.ActorsStopped {
		return errors.New("controller did not fence publication and reader")
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
			return errors.New("frozen witness digest changed: " + signal)
		}
		started := time.Now()
		var seen []int
		projected := 0
		switch signal {
		case "metrics":
			seen, err = metricReadback(ctx, backend, f)
		case "logs":
			seen, err = logReadback(ctx, backend, f)
		case "traces":
			seen, err = traceReadback(ctx, backend, f, allowProjection, &projected)
		}
		var counts reconciliation
		if err == nil {
			counts, err = history.reconcile(seen)
		}
		entry := map[string]any{"numeric_attribute_projection_spans": projected, "exact_attribute_type_fidelity": err == nil && projected == 0, "history_sha256": history.digest, "complete": err == nil, "counts": counts, "elapsed_s": time.Since(started).Seconds()}
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
	written := json.NewEncoder(file).Encode(map[string]any{"fixture": f, "backend": backend, "signals": results, "captured_at": time.Now().UTC(),
		"projection_diagnostic": allowProjection, "scope": "Frozen ACK/value reconciliation; numeric projection is explicitly marked and cannot establish exact attribute type fidelity"})
	return errors.Join(combined, written, file.Sync(), file.Close())
}
