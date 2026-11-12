package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"log"
	"math"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"time"

	chiceberg "github.com/example/iceberg-benchmark/clickhouse-iceberg"
	chnative "github.com/example/iceberg-benchmark/clickhouse-native"
	duckiceberg "github.com/example/iceberg-benchmark/duckdb-iceberg"
	workloadgenerator "github.com/example/iceberg-benchmark/workload-generator"
)

const (
	dataDir   = "/data/lake"
	inputDir  = "/bench-data"
	resultDir = "/results"
)

type engine interface {
	Setup(context.Context, string) error
	Append(context.Context, string) error
	Update(context.Context, string, int64) error
	Compact(context.Context) (map[string]any, error)
	Queries() map[string]string
	Query(context.Context, string, string) ([]map[string]any, error)
	Version(context.Context) (string, error)
	Close() error
}

type options struct {
	system, runID, profile string
	rows, appendRows       int64
	updateFraction         float64
	batches, runs          int
	cpus                   int
	memory                 string
}

type readResult struct {
	Results map[string][]map[string]any `json:"results"`
	Records []map[string]any            `json:"records"`
}

func main() {
	var o options
	flag.StringVar(&o.system, "system", "", "benchmark subsystem")
	flag.StringVar(&o.runID, "run-id", os.Getenv("BENCH_RUN_ID"), "shared benchmark run identifier")
	flag.StringVar(&o.profile, "profile", "mixed", "read-heavy, append-heavy, update-heavy, or mixed")
	flag.Int64Var(&o.rows, "rows", 20_000_000, "initial table rows")
	flag.Int64Var(&o.appendRows, "append-rows", -1, "rows in each append batch")
	flag.Float64Var(&o.updateFraction, "update-fraction", -1, "fraction of initial rows updated per batch")
	flag.IntVar(&o.batches, "batches", 1, "number of append/update batches")
	flag.IntVar(&o.runs, "runs", 5, "timed read repetitions per query")
	flag.IntVar(&o.cpus, "cpus", 4, "CPU threads")
	flag.StringVar(&o.memory, "memory", "6g", "memory limit metadata")
	flag.Parse()

	if err := run(o); err != nil {
		log.Fatal(err)
	}
}

func run(o options) error {
	if o.system != "duckdb-iceberg" && o.system != "clickhouse-iceberg" && o.system != "clickhouse-native" {
		return fmt.Errorf("unknown --system %q", o.system)
	}
	if o.rows < 1 || o.runs < 1 || o.batches < 1 || o.cpus < 1 {
		return errors.New("rows, runs, batches, and cpus must be positive")
	}
	defaultAppend, defaultUpdate := profileDefaults(o.profile, o.rows)
	if o.appendRows < 0 {
		o.appendRows = defaultAppend
	}
	if o.updateFraction < 0 {
		o.updateFraction = defaultUpdate
	}
	if o.appendRows < 0 || o.updateFraction < 0 || o.updateFraction > 1 {
		return errors.New("append-rows must be non-negative and update-fraction must be in [0, 1]")
	}
	updateRows := int64(math.Floor(float64(o.rows) * o.updateFraction))
	if o.updateFraction > 0 && updateRows == 0 {
		updateRows = 1
	}
	if o.runID == "" {
		o.runID = time.Now().UTC().Format("20060102T150405Z") + "-" + randomSuffix()
	}
	suffix := strings.NewReplacer("-", "_", ".", "_").Replace(o.runID)
	namespace := "bench_" + suffix
	nativeTable := "events_" + suffix
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Hour)
	defer cancel()

	inputs, err := workloadgenerator.Generate(ctx, inputDir, o.rows, o.appendRows, updateRows, o.batches)
	if err != nil {
		return fmt.Errorf("generate workload: %w", err)
	}
	eng, err := openEngine(o, namespace, nativeTable)
	if err != nil {
		return err
	}
	defer eng.Close()

	initialLoadMS, err := timed(func() error { return eng.Setup(ctx, inputs.Seed) })
	if err != nil {
		return fmt.Errorf("load seed: %w", err)
	}
	baseline, err := measureReads(ctx, eng, o.system, o.runs, "baseline")
	if err != nil {
		return err
	}
	appendSamples := make([]float64, 0, len(inputs.Appends))
	for _, batch := range inputs.Appends {
		elapsed, err := timed(func() error { return eng.Append(ctx, batch) })
		if err != nil {
			return fmt.Errorf("append %s: %w", filepath.Base(batch), err)
		}
		appendSamples = append(appendSamples, elapsed)
	}
	updateSamples := make([]float64, 0, len(inputs.Updates))
	stride := max(int64(1), o.rows/max(int64(1), updateRows))
	for _, batch := range inputs.Updates {
		elapsed, err := timed(func() error { return eng.Update(ctx, batch, stride) })
		if err != nil {
			return fmt.Errorf("update %s: %w", filepath.Base(batch), err)
		}
		updateSamples = append(updateSamples, elapsed)
	}
	final, err := measureReads(ctx, eng, o.system, o.runs, "final")
	if err != nil {
		return err
	}
	compaction, err := eng.Compact(ctx)
	if err != nil {
		return fmt.Errorf("compact table: %w", err)
	}
	postCompaction, err := measureReads(ctx, eng, o.system, o.runs, "post_compaction")
	if err != nil {
		return err
	}
	if !equalJSON(final.Results, postCompaction.Results) {
		return errors.New("logical query results changed during compaction")
	}
	version, err := eng.Version(ctx)
	if err != nil {
		return fmt.Errorf("read engine version: %w", err)
	}

	appendTotal := int64(len(appendSamples)) * o.appendRows
	updateTotal := int64(len(updateSamples)) * updateRows
	writeTimings := map[string]any{
		"append_batch_ms": summarize(appendSamples),
		"update_batch_ms": summarize(updateSamples),
	}
	if total := sum(appendSamples); total > 0 {
		writeTimings["append_rows_per_second"] = float64(appendTotal) / (total / 1000)
	}
	if total := sum(updateSamples); total > 0 {
		writeTimings["update_rows_per_second"] = float64(updateTotal) / (total / 1000)
	}
	versions := map[string]string{"duckdb": inputs.DuckDBVersion}
	if o.system == "clickhouse-iceberg" || o.system == "clickhouse-native" {
		versions["clickhouse"] = version
	}
	result := map[string]any{
		"run_id":         o.runID,
		"system":         o.system,
		"created_at_utc": time.Now().UTC().Format(time.RFC3339Nano),
		"profile":        o.profile,
		"parameters": map[string]any{
			"initial_rows": o.rows, "append_rows_per_batch": o.appendRows,
			"update_fraction_per_batch": o.updateFraction, "update_rows_per_batch": updateRows,
			"write_batches": o.batches, "read_runs": o.runs, "cpu_limit": o.cpus, "memory_limit": o.memory,
		},
		"versions":                versions,
		"machine":                 map[string]any{"go_version": runtime.Version(), "os": runtime.GOOS, "arch": runtime.GOARCH, "cpu_count_visible": runtime.NumCPU()},
		"initial_load_ms":         initialLoadMS,
		"write_timings":           writeTimings,
		"compaction":              compaction,
		"baseline_results":        baseline.Results,
		"final_results":           final.Results,
		"post_compaction_results": postCompaction.Results,
		"reads":                   append(append(baseline.Records, final.Records...), postCompaction.Records...),
	}
	if err := os.MkdirAll(resultDir, 0o755); err != nil {
		return err
	}
	path := filepath.Join(resultDir, fmt.Sprintf("iceberg-benchmark-%s-%s.json", o.runID, o.system))
	contents, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(path, append(contents, '\n'), 0o644); err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{
		"result_file": path, "system": o.system, "initial_load_ms": initialLoadMS,
		"write_timings": writeTimings, "compaction": compaction,
	})
}

func openEngine(o options, namespace, nativeTable string) (engine, error) {
	switch o.system {
	case "duckdb-iceberg":
		return duckiceberg.Open(namespace, o.rows, dataDir)
	case "clickhouse-iceberg":
		return chiceberg.Open(os.Getenv("CLICKHOUSE_ADDR"), namespace, o.rows, dataDir, o.cpus)
	default:
		return chnative.Open(os.Getenv("CLICKHOUSE_ADDR"), nativeTable, o.rows, o.cpus)
	}
}

func profileDefaults(profile string, rows int64) (int64, float64) {
	switch profile {
	case "read-heavy":
		return 0, 0
	case "append-heavy":
		return max(int64(1000), rows/10), 0
	case "update-heavy":
		return 0, 0.1
	case "mixed":
		return max(int64(1000), rows/100), 0.01
	default:
		return -1, -1
	}
}

func measureReads(ctx context.Context, eng engine, system string, runs int, phase string) (readResult, error) {
	queries := eng.Queries()
	result := readResult{Results: make(map[string][]map[string]any, len(queries))}
	for _, name := range []string{"full_scan", "group_by", "selective"} {
		query := queries[name]
		warmup, err := eng.Query(ctx, name, query)
		if err != nil {
			return readResult{}, fmt.Errorf("%s %s warmup: %w", phase, name, err)
		}
		result.Results[name] = warmup
		times := make([]float64, 0, runs)
		for run := 1; run <= runs; run++ {
			start := time.Now()
			actual, err := eng.Query(ctx, name, query)
			elapsed := float64(time.Since(start).Microseconds()) / 1000
			if err != nil {
				return readResult{}, fmt.Errorf("%s %s run %d: %w", phase, name, run, err)
			}
			if !equalJSON(actual, warmup) {
				return readResult{}, fmt.Errorf("result changed during %s read %s", phase, name)
			}
			times = append(times, elapsed)
			result.Records = append(result.Records, map[string]any{"system": system, "phase": phase, "query": name, "run": run, "client_wall_ms": elapsed})
		}
		sort.Float64s(times)
		result.Records = append(result.Records, map[string]any{
			"system": system, "phase": phase, "query": name,
			"median_client_wall_ms": percentile(times, 0.5), "p95_client_wall_ms": percentile(times, 0.95),
		})
	}
	return result, nil
}

func equalJSON(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return string(left) == string(right)
}

func timed(fn func() error) (float64, error) {
	start := time.Now()
	err := fn()
	return float64(time.Since(start).Microseconds()) / 1000, err
}

func percentile(sortedValues []float64, p float64) float64 {
	if len(sortedValues) == 0 {
		return 0
	}
	index := int(math.Ceil(p*float64(len(sortedValues)))) - 1
	return sortedValues[max(0, min(index, len(sortedValues)-1))]
}

func summarize(samples []float64) map[string]any {
	if len(samples) == 0 {
		return map[string]any{"count": 0}
	}
	values := append([]float64(nil), samples...)
	sort.Float64s(values)
	return map[string]any{"count": len(values), "median_ms": percentile(values, .5), "p95_ms": percentile(values, .95), "min_ms": values[0], "max_ms": values[len(values)-1]}
}

func sum(values []float64) float64 {
	var total float64
	for _, value := range values {
		total += value
	}
	return total
}

func randomSuffix() string {
	bytes := make([]byte, 3)
	if _, err := rand.Read(bytes); err != nil {
		return fmt.Sprintf("%06x", time.Now().UnixNano()&0xffffff)
	}
	return hex.EncodeToString(bytes)
}
