package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
)

func validateFixture(f Fixture) error {
	identifier := regexp.MustCompile(`^[a-f0-9]{32}$`)
	if !identifier.MatchString(f.Run) || !identifier.MatchString(f.PayloadSeed) || f.StartMS <= 0 || f.StartMS > math.MaxInt64/1000000-int64(f.Requests)*10-100000 || f.Requests < 1 || f.Requests > 10000 || f.LogsPerRequest < 1 || f.LogsPerRequest > 100 || f.MetricPoints < 1 || f.MetricPoints > 100 {
		return errors.New("fixture outside declared bounds")
	}
	return nil
}

func health(ctx context.Context, backend string) error {
	endpoints := map[string][]string{
		"greptime": {"http://greptime:4000/health"},
		"victoria": {"http://vmetrics:8428/health", "http://vlogs:9428/health", "http://vtraces:10428/health"},
		"grafana":  {"http://prometheus:9090/-/ready", "http://loki:3100/ready", "http://tempo:3200/ready"},
	}
	for _, address := range endpoints[backend] {
		response, err := get(ctx, address, "")
		if err != nil {
			return err
		}
		_, readErr := io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		if err = errors.Join(readErr, response.Body.Close()); err != nil {
			return err
		}
	}
	host := strings.Split(collectorHosts[backend], ":")[0]
	response, err := get(ctx, "http://"+host+":13133", "")
	if err != nil {
		return err
	}
	defer response.Body.Close()
	_, err = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	return err
}

func inspectFixture(ctx context.Context, backend string, f Fixture, id int) error {
	var endpoints []string
	if backend == "greptime" {
		for _, query := range []string{fmt.Sprintf("SELECT * FROM checkout_traces_v2 WHERE trace_id='%s' LIMIT 4", hex.EncodeToString(f.traceID(id))), fmt.Sprintf("SELECT * FROM opentelemetry_logs WHERE run='%s' LIMIT 1", f.Run)} {
			endpoints = append(endpoints, "http://greptime:4000/v1/sql?"+url.Values{"db": {"public"}, "sql": {query}}.Encode())
		}
	} else {
		endpoint := "http://tempo:3200/api/v2/traces/"
		if backend == "victoria" {
			endpoint = "http://vtraces:10428/select/tempo/api/v2/traces/"
		}
		endpoints = append(endpoints, endpoint+hex.EncodeToString(f.traceID(id)))
	}
	for _, endpoint := range endpoints {
		response, err := get(ctx, endpoint, "application/json")
		if err != nil {
			return err
		}
		raw, readErr := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
		closeErr := response.Body.Close()
		if err = errors.Join(readErr, closeErr); err != nil {
			return err
		}
		if len(raw) > 1<<20 {
			return errors.New("inspection response exceeded 1 MiB")
		}
		if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"endpoint": endpoint, "response": string(raw), "meaning": "Native field-mapping inspection, not a correctness verdict"}); err != nil {
			return err
		}
	}
	return nil
}

func execute(ctx context.Context, args []string) error {
	flags := flag.NewFlagSet("incident", flag.ContinueOnError)
	mode := flags.String("mode", "publish", "publish, watch, verify, health, inspect, manifest, disk or filesystem")
	metricRole := flags.String("metric-role", "", "Exact example-owned role to scrape")
	dataUID := flags.Int("data-uid", 0, "Native service UID for an empty owned data volume")
	backend := flags.String("backend", "", "greptime, victoria or grafana")
	inspectID := flags.Int("inspect-request", 0, "Request ID for native trace projection inspection")
	reportPath := flags.String("report", "", "Exclusive final readback report")
	priorPath := flags.String("prior-fixture", "", "Previously verified immutable fixture for the live reader")
	fixturePath := flags.String("fixture", "/results/fixture.json", "Persisted common corpus")
	output := flags.String("output", "/results/publication", "Exclusive publication directory")
	pace := flags.Duration("pace", 200*time.Millisecond, "Closed-loop delay after each already-batched request")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return errors.New("unexpected positional arguments")
	}
	switch *mode {
	case "manifest":
		raw, err := os.ReadFile("/source.sha256")
		if err != nil {
			return err
		}
		_, err = os.Stdout.Write(raw)
		return err
	case "scrape":
		endpoints := map[string]string{"greptime": "greptime:4000", "vmetrics": "vmetrics:8428", "vlogs": "vlogs:9428", "vtraces": "vtraces:10428", "prometheus": "prometheus:9090", "loki": "loki:3100", "tempo": "tempo:3200", "greptime-collector": "greptime-collector:8888", "victoria-collector": "victoria-collector:8888", "grafana-collector": "grafana-collector:8888"}
		host, ok := endpoints[*metricRole]
		if !ok {
			return errors.New("unknown native metrics role")
		}
		response, err := get(ctx, "http://"+host+"/metrics", "")
		if err != nil {
			return err
		}
		defer response.Body.Close()
		n, err := io.Copy(os.Stdout, io.LimitReader(response.Body, (16<<20)+1))
		if n > 16<<20 {
			return errors.New("native metrics response exceeds 16 MiB")
		}
		return err
	case "prepare-data":
		if *dataUID != 10001 && *dataUID != 65534 {
			return errors.New("unsupported lab data UID")
		}
		info, err := os.Stat("/data")
		if err != nil {
			return err
		}
		owner, ok := info.Sys().(*syscall.Stat_t)
		if !ok {
			return errors.New("cannot inspect data owner")
		}
		if owner.Uid == uint32(*dataUID) {
			return nil
		}
		entries, err := os.ReadDir("/data")
		if err != nil {
			return err
		}
		if len(entries) != 0 {
			return errors.New("refuse to change ownership of nonempty data")
		}
		if err = os.Chmod("/data", 0750); err != nil {
			return err
		}
		return os.Chown("/data", *dataUID, *dataUID)
	case "disk":
		return diskSnapshot()
	case "filesystem":
		return filesystemSnapshot()
	}
	if _, ok := collectorHosts[*backend]; !ok {
		return errors.New("unknown isolated backend")
	}
	if *mode == "health" {
		return health(ctx, *backend)
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
	if *mode == "inspect" {
		if *inspectID < 0 || *inspectID >= fixture.Requests {
			return errors.New("inspection request outside fixture")
		}
		return inspectFixture(ctx, *backend, fixture, *inspectID)
	}
	if *mode == "verify" || *mode == "verify-projection" {
		if *reportPath == "" {
			return errors.New("an exclusive report is required")
		}
		bounded, cancel := context.WithTimeout(ctx, 10*time.Minute)
		defer cancel()
		return verify(bounded, *backend, fixture, *output, *reportPath, *mode == "verify-projection")
	}
	if *mode == "watch" {
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
		bounded, cancel := context.WithTimeout(ctx, 10*time.Minute)
		defer cancel()
		return watch(bounded, *backend, fixture, prior, *output)
	}
	if *mode != "publish" {
		return errors.New("unsupported mode")
	}
	if *pace < 0 || *pace > time.Second {
		return errors.New("pace outside 0..1s")
	}
	if err = os.Mkdir(*output, 0755); err != nil {
		return err
	}
	config := map[string]any{"corpus_version": "checkout-incident-v1", "fixture": fixture, "backend": *backend, "mode": *mode, "collector_endpoint": "http://" + collectorHosts[*backend], "pace_ms": float64(pace.Nanoseconds()) / 1e6,
		"workers": 3, "metric_log_batch": 1000, "trace_request_batch": 10, "trace_span_batch": 30, "retry_attempts": 0, "request_timeout_s": 30, "connect_timeout_s": 5, "maximum_connections_per_host": 4, "compression": "gzip", "started_at": time.Now().UTC(), "maximum_publication_s": 600}
	file, err := os.OpenFile(filepath.Join(*output, "configuration.json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	written := json.NewEncoder(file).Encode(config)
	if err = errors.Join(written, file.Sync(), file.Close()); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Minute)
	defer cancel()
	return publish(ctx, *backend, fixture, *output, *pace)
}

func emitResources() {
	values, unavailable := make(map[string]string), make(map[string]string)
	for _, name := range []string{"cpu.stat", "memory.current", "memory.peak", "io.stat"} {
		raw, err := os.ReadFile("/sys/fs/cgroup/" + name)
		if err != nil {
			unavailable[name] = err.Error()
		} else {
			values[name] = string(raw)
		}
	}
	_ = json.NewEncoder(os.Stderr).Encode(map[string]any{"event": "native-cgroup-before-exit", "captured_at": time.Now().UTC(), "values": values, "unavailable": unavailable, "scope": "Whole container/process lifetime"})
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
