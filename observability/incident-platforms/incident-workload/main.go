package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func validateFixture(f Fixture) error {
	for _, id := range []string{f.Run, f.PayloadSeed} {
		decoded, err := hex.DecodeString(id)
		if err != nil || len(decoded) != 16 {
			return errors.New("fixture identifiers must be 128-bit hex")
		}
	}
	if f.Requests < 1 || f.Requests > 10000 || f.MetricPoints < 1 || f.MetricPoints > 100 || f.LogsPerRequest < 1 || f.LogsPerRequest > 100 || f.StartMS <= 0 || f.StartMS > math.MaxInt64/1000000-int64(f.Requests)*10-100000 {
		return errors.New("fixture exceeds this bounded local experiment")
	}
	return nil
}

func execute(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("incident-platform", flag.ContinueOnError)
	mode := flags.String("mode", "publish", "publish, manifest, health, schema, sample, verify, queries, filesystem or disk")
	report := flags.String("report", "", "Exclusive verification result JSON")
	priorPath := flags.String("prior-fixture", "", "Previously verified fixture for live old-data reads")
	backend := flags.String("backend", "", "clickstack, openobserve, signoz or grafana")
	fixture := flags.String("fixture", "", "Example-owned immutable fixture JSON")
	output := flags.String("output", "", "Exclusive journal directory")
	pace := flags.Duration("pace", 200*time.Millisecond, "Closed-loop delay after each batch")
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
	if (*mode == "health" || *mode == "schema") && *backend != "" && *backend != "openobserve" {
		return errors.New("native endpoint not implemented for this backend yet")
	}
	if *mode == "health" {
		_, err := openObserveJSON(ctx, "GET", "/healthz", nil)
		return err
	}
	if *mode == "schema" {
		return inspectOpenObserve(ctx)
	}
	if *mode == "manifest" {
		raw, err := os.ReadFile("/source.sha256")
		if err != nil {
			return err
		}
		_, err = os.Stdout.Write(raw)
		return err
	}
	if (*mode != "publish" && *mode != "sample" && *mode != "verify" && *mode != "watch" && *mode != "queries") || ((*mode == "publish" || *mode == "verify" || *mode == "watch") && *output == "") {
		return errors.New("publish requires an exclusive output directory")
	}
	raw, err := os.ReadFile(*fixture)
	if err != nil {
		return err
	}
	var f Fixture
	if err = json.Unmarshal(raw, &f); err != nil {
		return err
	}
	if err = validateFixture(f); err != nil {
		return err
	}
	if *pace < 0 {
		return errors.New("negative source pace")
	}
	if *mode == "watch" {
		if *backend != "openobserve" {
			return errors.New("live native reader is not implemented for this backend yet")
		}
		priorRaw, err := os.ReadFile(*priorPath)
		if err != nil {
			return err
		}
		var prior Fixture
		if err = json.Unmarshal(priorRaw, &prior); err != nil {
			return err
		}
		if err = validateFixture(prior); err != nil {
			return err
		}
		ctx, cancel := context.WithTimeout(ctx, 15*time.Minute)
		defer cancel()
		return watchOpenObserve(ctx, f, prior, *output)
	}
	if *mode == "sample" || *mode == "verify" || *mode == "queries" {
		if *backend != "openobserve" {
			return errors.New("native readback for this platform is not implemented yet")
		}
		if *mode == "sample" {
			return sampleOpenObserve(ctx, f)
		}
		if *report == "" {
			return errors.New("verification requires an exclusive report path")
		}
		ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
		defer cancel()
		if *mode == "queries" {
			return incidentQueries(ctx, f, *report)
		}
		return verifyOpenObserve(ctx, f, *output, *report)
	}
	if _, ok := collectorHosts[*backend]; !ok {
		return errors.New("unknown isolated platform")
	}
	if err = os.MkdirAll(*output, 0755); err != nil {
		return err
	}
	configuration, err := os.OpenFile(filepath.Join(*output, "configuration.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	written := json.NewEncoder(configuration).Encode(map[string]any{
		"fixture": f, "backend": *backend, "collector_host": collectorHosts[*backend],
		"driver": "Go net/http and official OTLP protobuf", "request_timeout_s": 30,
		"connect_timeout_s": 5, "max_connections_per_host": 4,
		"application_retries": 0, "pace_ms": float64(pace.Milliseconds()),
		"source_admission": "HTTP success and no OTLP partial rejection; not a synchronous backend durability assertion",
	})
	if err = errors.Join(written, configuration.Sync(), configuration.Close()); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	return publish(ctx, *backend, f, *output, *pace)
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	err := execute(ctx, os.Args[1:])
	emitResources()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
