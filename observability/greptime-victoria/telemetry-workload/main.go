package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"syscall"
	"time"
)

func execute(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("telemetry", flag.ContinueOnError)
	protocol := flags.String("protocol", "otlp", "otlp or native")
	mode := flags.String("mode", "push", "push, verify, health or manifest")
	missingReport := flags.String("missing-report", "", "Preserved pre-replay missing-ack report")
	ackCutoff := flags.String("ack-before", "", "Restart timestamp; replay only older acknowledged records")
	priorPath := flags.String("prior-fixture", "", "Previously verified immutable corpus for the live reader")
	reportPath := flags.String("report", "", "New exclusive history-reconciliation report path")
	historyPath := flags.String("history", "", "Request journal; defaults to output/requests.jsonl")
	fixturePath := flags.String("fixture", "/results/fixture.json", "Persisted common corpus identity")
	output := flags.String("output", "/results/trial", "Owned trial evidence directory")
	backend := flags.String("backend", "", "greptime or victoria")
	signalName := flags.String("signal", "metrics", "metrics or logs")
	batch := flags.Int("batch", 1000, "Records per request")
	compressed := flags.Bool("gzip", true, "Compress OTLP protobuf requests identically")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected positional arguments")
	}
	if *mode == "filesystem" {
		return filesystemSnapshot()
	}
	if *mode == "disk" {
		return diskSnapshot()
	}
	if *mode == "manifest" {
		raw, err := os.ReadFile("/source.sha256")
		if err != nil {
			return err
		}
		_, err = os.Stdout.Write(raw)
		return err
	}
	if *mode == "health" {
		return health(ctx, *backend)
	}
	if *mode != "push" && *mode != "verify" && *mode != "live" && *mode != "verify-history" && *mode != "replay" {
		return errors.New("invalid mode")
	}
	if *protocol != "otlp" && *protocol != "native" {
		return errors.New("invalid protocol")
	}
	if *batch < 1 || *batch > 10000 {
		return errors.New("batch must be 1..10000")
	}
	raw, err := os.ReadFile(*fixturePath)
	if err != nil {
		return err
	}
	var fixture Fixture
	if err = json.Unmarshal(raw, &fixture); err != nil {
		return err
	}
	if err = validateFixture(fixture); err != nil {
		return err
	}
	total := fixture.Series * fixture.Points
	if total > 2000000 {
		return errors.New("metric fixture exceeds two million points")
	}
	var endpoint string
	switch *backend + ":" + *signalName {
	case "greptime:metrics":
		endpoint = "http://greptime:4000/v1/otlp/v1/metrics"
	case "greptime:logs":
		endpoint = "http://greptime:4000/v1/otlp/v1/logs"
	case "victoria:metrics":
		endpoint = "http://vmetrics:8428/opentelemetry/v1/metrics"
	case "victoria:logs":
		endpoint = "http://vlogs:9428/insert/opentelemetry/v1/logs"
	default:
		return errors.New("invalid backend/signal")
	}
	if *signalName == "logs" {
		total = fixture.Logs
	}
	if *mode == "verify-history" {
		if *reportPath == "" {
			return errors.New("an exclusive --report path is required")
		}
		journalPath := *historyPath
		if journalPath == "" {
			journalPath = filepath.Join(*output, "requests.jsonl")
		}
		history, err := readHistory(journalPath, total)
		if err != nil {
			return err
		}
		var outcome reconciliation
		if *signalName == "metrics" {
			name := "fleet_value"
			if *protocol == "native" {
				name = "fleet_native"
			}
			outcome, err = metricReadback(ctx, *backend, fixture, name, history)
		} else {
			outcome, err = logReadback(ctx, *backend, fixture, *protocol, history)
		}
		result := map[string]any{"fixture": fixture, "backend": *backend, "signal": *signalName, "protocol": *protocol, "mode": *mode, "live_think_ms": 200, "live_reader_period_ms": 1000, "history_sha256": history.digest, "result": outcome, "complete": err == nil}
		if err != nil {
			result["error"] = err.Error()
		}
		saved := saveExclusiveJSON(*reportPath, result)
		return errors.Join(err, saved)
	}
	if *mode == "verify" {
		began := time.Now()
		if *signalName == "metrics" {
			name := "fleet_value"
			if *protocol == "native" {
				name = "fleet_native"
			}
			err = verifyMetrics(ctx, *backend, fixture, name)
		} else {
			err = verifyLogs(ctx, *backend, fixture, *protocol)
		}
		if err != nil {
			return err
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "exact-readback", "backend": *backend, "signal": *signalName, "expected_records": total, "missing": 0, "duplicates": 0, "wrong": 0, "duration_s": time.Since(began).Seconds()})
	}
	// A completed/failed trial is immutable. Recover using its journal, not a
	// second push over a partially accepted append-only log fixture.
	if _, err = os.Stat(filepath.Join(*output, "requests.jsonl")); err == nil {
		return errors.New("trial journal already exists; reconcile it instead of replaying")
	} else if !os.IsNotExist(err) {
		return err
	}
	journal, err := openJournal(*output)
	if err != nil {
		return err
	}
	defer journal.file.Close()
	metadata := map[string]any{"fixture": fixture, "backend": *backend, "signal": *signalName, "protocol": *protocol, "mode": *mode, "live_think_ms": 200, "live_reader_period_ms": 1000, "requested_otlp_gzip": *compressed, "batch": *batch, "workers": 1, "retry_attempts": 0, "http_timeout_s": 30, "connect_timeout_s": 5, "max_connections_per_host": 4, "started_at": time.Now().UTC()}
	data, err := json.MarshalIndent(metadata, "", "  ")
	if err != nil {
		return err
	}
	if err = os.WriteFile(filepath.Join(*output, "configuration.json"), append(data, '\n'), 0644); err != nil {
		return err
	}
	if *protocol == "native" && *signalName == "logs" && *backend == "greptime" {
		if err = setupNativeLogs(ctx); err != nil {
			return err
		}
	}
	if *mode == "replay" {
		if *protocol != "otlp" {
			return errors.New("witness replay is qualified only for the common OTLP path")
		}
		return replayMissing(ctx, journal, *backend, *signalName, fixture, *historyPath, *missingReport, *ackCutoff, *compressed)
	}
	if *mode == "live" {
		if *protocol != "otlp" || *priorPath == "" {
			return errors.New("live mode requires common OTLP and a prior verified fixture")
		}
		raw, err := os.ReadFile(*priorPath)
		if err != nil {
			return err
		}
		var prior Fixture
		if err = json.Unmarshal(raw, &prior); err != nil {
			return err
		}
		if err = validateFixture(prior); err != nil {
			return err
		}
		return runLive(ctx, journal, *backend, *signalName, fixture, prior, *batch, *compressed, *output)
	}
	began := time.Now()
	for first := 0; first < total; first += *batch {
		last := min(first+*batch, total)
		if *protocol == "native" {
			if err = sendNative(ctx, journal, *backend, *signalName, fixture, first, last); err != nil {
				return err
			}
			continue
		}
		if err = send(ctx, journal, endpoint, *signalName, first, last, fixture, *compressed); err != nil {
			return fmt.Errorf("records [%d,%d): %w; retain journal for reconciliation", first, last, err)
		}
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"event": "admission-complete", "records": total, "elapsed_s": time.Since(began).Seconds(), "meaning": "API acceptance, not verified visibility or crash durability"})
}

func validateFixture(f Fixture) error {
	identifier := regexp.MustCompile(`^[a-f0-9]{32}$`)
	if !identifier.MatchString(f.Run) || (f.PayloadSeed != "" && !identifier.MatchString(f.PayloadSeed)) || f.Series < 1 || f.Series > 100000 || f.Points < 1 || f.Points > 10000 || f.Logs < 1 || f.Logs > 2000000 || f.StartMS <= 0 || f.Series*f.Points > 2000000 {
		return errors.New("fixture outside supported bounds")
	}
	return nil
}

func saveExclusiveJSON(path string, value any) error {
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	written := json.NewEncoder(file).Encode(value)
	synced := file.Sync()
	closed := file.Close()
	return errors.Join(written, synced, closed)
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	err := execute(ctx, os.Args[1:])
	emitResources()
	if err != nil {
		_ = json.NewEncoder(os.Stderr).Encode(map[string]any{"event": "failed", "error": err.Error()})
		os.Exit(1)
	}
}
