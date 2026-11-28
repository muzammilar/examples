package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"
)

type historyReport struct {
	Fixture  Fixture        `json:"fixture"`
	Backend  string         `json:"backend"`
	Signal   string         `json:"signal"`
	Protocol string         `json:"protocol"`
	Digest   string         `json:"history_sha256"`
	Complete bool           `json:"complete"`
	Result   reconciliation `json:"result"`
}

func replayMissing(ctx context.Context, journal *Journal, backend, signal string, f Fixture, historyPath, reportPath, cutoff string, compressed bool) error {
	if historyPath == "" || reportPath == "" || cutoff == "" {
		return errors.New("replay requires original history, missing report and old-process acknowledgement cutoff")
	}
	limit := f.Series * f.Points
	if signal == "logs" {
		limit = f.Logs
	}
	history, err := readHistory(historyPath, limit)
	if err != nil {
		return err
	}
	raw, err := os.ReadFile(reportPath)
	if err != nil {
		return err
	}
	var planned historyReport
	if err = json.Unmarshal(raw, &planned); err != nil {
		return err
	}
	if !planned.Complete || planned.Fixture != f || planned.Backend != backend || planned.Signal != signal || planned.Protocol != "otlp" || planned.Digest != history.digest {
		return errors.New("replay report does not match the frozen source history/fixture")
	}
	cut, err := time.Parse(time.RFC3339Nano, cutoff)
	if err != nil {
		return err
	}
	var current reconciliation
	if signal == "metrics" {
		current, err = metricReadback(ctx, backend, f, "fleet_value", history)
	} else {
		current, err = logReadback(ctx, backend, f, "otlp", history)
	}
	if err != nil {
		return err
	}
	eligible := make(map[int]bool, len(planned.Result.MissingAcknowledged))
	for _, id := range planned.Result.MissingAcknowledged {
		eligible[id] = true
	}
	// Validate every candidate before making any mutation. An acknowledgement
	// from the restarted process may still be pending: never replay it here.
	for _, id := range current.MissingAcknowledged {
		if !eligible[id] || !history.acknowledgedBefore(id, cut) {
			return fmt.Errorf("id %d is not an eligible witnessed old-process acknowledgement", id)
		}
	}
	address := "http://greptime:4000/v1/otlp/v1/" + signal
	if backend == "victoria" {
		address = "http://vmetrics:8428/opentelemetry/v1/metrics"
		if signal == "logs" {
			address = "http://vlogs:9428/insert/opentelemetry/v1/logs"
		}
	}
	began := time.Now()
	missing := current.MissingAcknowledged
	for position := 0; position < len(missing); {
		first := missing[position]
		end := position + 1
		for end < len(missing) && end-position < 1000 && missing[end] == missing[end-1]+1 {
			end++
		}
		last := missing[end-1] + 1
		if err = send(ctx, journal, address, signal, first, last, f, compressed); err != nil {
			return fmt.Errorf("replay outcome may be ambiguous; preserve its new journal before retrying: %w", err)
		}
		position = end
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "external-witness-replay", "replayed_records": len(missing), "already_visible_without_replay": len(planned.Result.MissingAcknowledged) - len(missing), "replay_s": time.Since(began).Seconds(), "source_history_sha256": history.digest, "meaning": "application witness replay, not native database recovery; original loss evidence remains unchanged"})
}
