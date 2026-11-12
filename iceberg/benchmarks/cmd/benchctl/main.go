package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math/rand/v2"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

var systems = []string{"duckdb-iceberg", "clickhouse-iceberg", "clickhouse-native"}

type benchmarkResult struct {
	RunID       string                      `json:"run_id"`
	System      string                      `json:"system"`
	Profile     string                      `json:"profile"`
	Parameters  map[string]any              `json:"parameters"`
	Versions    map[string]string           `json:"versions"`
	InitialLoad float64                     `json:"initial_load_ms"`
	Writes      map[string]any              `json:"write_timings"`
	Compaction  map[string]any              `json:"compaction"`
	Baseline    map[string][]map[string]any `json:"baseline_results"`
	Final       map[string][]map[string]any `json:"final_results"`
	PostCompact map[string][]map[string]any `json:"post_compaction_results"`
	Reads       []map[string]any            `json:"reads"`
}

type comparison struct {
	RunID       string                   `json:"run_id"`
	Profile     string                   `json:"profile"`
	Parameters  map[string]any           `json:"parameters"`
	Correctness string                   `json:"correctness"`
	Systems     map[string]systemSummary `json:"systems"`
}

type systemSummary struct {
	Versions      map[string]string  `json:"versions"`
	InitialLoadMS float64            `json:"initial_load_ms"`
	WriteTimings  map[string]any     `json:"write_timings"`
	Compaction    map[string]any     `json:"compaction"`
	ReadMedian    map[string]float64 `json:"post_compaction_read_median_ms"`
}

func main() {
	var runID, profile, memory string
	var rows, appendRows int64
	var updateFraction float64
	var runs, batches, cpus int
	flag.StringVar(&runID, "run-id", "", "shared benchmark run identifier")
	flag.StringVar(&profile, "profile", "mixed", "read-heavy, append-heavy, update-heavy, or mixed")
	flag.Int64Var(&rows, "rows", 20_000_000, "initial table rows")
	flag.Int64Var(&appendRows, "append-rows", -1, "rows in each append batch")
	flag.Float64Var(&updateFraction, "update-fraction", -1, "fraction of rows updated per batch")
	flag.IntVar(&runs, "runs", 5, "timed reads per query")
	flag.IntVar(&batches, "batches", 1, "append/update batches")
	flag.IntVar(&cpus, "cpus", 4, "CPU limit")
	flag.StringVar(&memory, "memory", "6g", "memory limit")
	flag.Parse()
	if runID == "" {
		runID = time.Now().UTC().Format("20060102T150405Z") + fmt.Sprintf("-%06x", rand.Uint32()&0xffffff)
	}
	if err := execute(runID, profile, rows, appendRows, updateFraction, runs, batches, cpus, memory); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func execute(runID, profile string, rows, appendRows int64, updateFraction float64, runs, batches, cpus int, memory string) error {
	if rows < 1 || runs < 1 || batches < 1 || cpus < 1 {
		return errors.New("rows, runs, batches, and cpus must be positive")
	}
	root, err := os.Getwd()
	if err != nil {
		return err
	}
	compose := []string{"compose", "-f", "compose.yml"}
	env := append(os.Environ(), "BENCH_CPUS="+fmt.Sprint(cpus), "BENCH_MEM="+memory, "BENCH_RUN_ID="+runID)
	results := make([]benchmarkResult, 0, len(systems))
	for _, system := range systems {
		profileArgs := []string{"--profile", system}
		service := "bench-" + system
		if err := command(root, env, append(append(compose, profileArgs...), "build", service)...); err != nil {
			return err
		}
		args := append(append(append([]string{}, compose...), profileArgs...), "run", "--rm", service,
			"--system", system, "--run-id", runID, "--profile", profile,
			"--rows", fmt.Sprint(rows), "--runs", fmt.Sprint(runs), "--batches", fmt.Sprint(batches),
			"--cpus", fmt.Sprint(cpus), "--memory", memory)
		if appendRows >= 0 {
			args = append(args, "--append-rows", fmt.Sprint(appendRows))
		}
		if updateFraction >= 0 {
			args = append(args, "--update-fraction", fmt.Sprint(updateFraction))
		}
		runErr := command(root, env, args...)
		downErr := command(root, env, append(append(compose, profileArgs...), "down", "--remove-orphans")...)
		if runErr != nil {
			return runErr
		}
		if downErr != nil {
			return downErr
		}
		path := filepath.Join(root, "results", fmt.Sprintf("iceberg-benchmark-%s-%s.json", runID, system))
		content, err := os.ReadFile(path)
		if err != nil {
			return err
		}
		var result benchmarkResult
		if err := json.Unmarshal(content, &result); err != nil {
			return err
		}
		results = append(results, result)
	}
	return writeComparison(root, runID, results)
}

func command(dir string, env []string, args ...string) error {
	fmt.Println("$ docker " + strings.Join(args, " "))
	cmd := exec.Command("docker", args...)
	cmd.Dir = dir
	cmd.Env = env
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	return cmd.Run()
}

func writeComparison(root, runID string, results []benchmarkResult) error {
	if len(results) != len(systems) {
		return fmt.Errorf("got %d system results; expected %d", len(results), len(systems))
	}
	ref := results[0]
	comparisonResult := comparison{
		RunID:       runID,
		Profile:     ref.Profile,
		Parameters:  ref.Parameters,
		Correctness: "all systems returned identical baseline, final, and post-compaction query results",
		Systems:     make(map[string]systemSummary, len(results)),
	}
	for _, result := range results {
		if !equal(result.Parameters, ref.Parameters) || !equal(result.Baseline, ref.Baseline) || !equal(result.Final, ref.Final) || !equal(result.PostCompact, ref.PostCompact) {
			return fmt.Errorf("%s result does not match the other systems", result.System)
		}
		reads := make(map[string]float64)
		for _, record := range result.Reads {
			if record["phase"] == "post_compaction" {
				name, _ := record["query"].(string)
				if value, ok := record["median_client_wall_ms"].(float64); ok {
					reads[name] = value
				}
			}
		}
		comparisonResult.Systems[result.System] = systemSummary{
			Versions: result.Versions, InitialLoadMS: result.InitialLoad, WriteTimings: result.Writes,
			Compaction: result.Compaction, ReadMedian: reads,
		}
	}
	output := filepath.Join(root, "results", "iceberg-comparison-"+runID+".json")
	data, err := json.MarshalIndent(comparisonResult, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(output, append(data, '\n'), 0o644); err != nil {
		return err
	}
	fmt.Println("\nBenchmark comparison (client wall time, ms)")
	fmt.Printf("Profile: %s; rows: %v; correctness: %s\n", comparisonResult.Profile, ref.Parameters["initial_rows"], comparisonResult.Correctness)
	fmt.Printf("%-24s %12s %12s %12s %12s %12s %12s %12s\n", "system", "load ms", "full scan", "group by", "selective", "append ms", "update ms", "compact ms")
	for _, system := range systems {
		value := comparisonResult.Systems[system]
		fmt.Printf("%-24s %12.1f %12.1f %12.1f %12.1f %12.1f %12.1f %12s\n", system, value.InitialLoadMS,
			value.ReadMedian["full_scan"], value.ReadMedian["group_by"], value.ReadMedian["selective"],
			medianWrite(value.WriteTimings, "append_batch_ms"), medianWrite(value.WriteTimings, "update_batch_ms"), compactTime(value.Compaction))
	}
	fmt.Printf("\nSaved comparison: %s\n", output)
	return nil
}

func compactTime(compaction map[string]any) string {
	if status, _ := compaction["status"].(string); status == "unavailable" {
		return "n/a"
	}
	if value, ok := compaction["data_file_rewrite_ms"].(float64); ok {
		return fmt.Sprintf("%.1f", value)
	}
	operations, _ := compaction["operations"].([]any)
	var total float64
	for _, operation := range operations {
		if fields, ok := operation.(map[string]any); ok {
			if value, ok := fields["elapsed_ms"].(float64); ok {
				total += value
			}
		}
	}
	return fmt.Sprintf("%.1f", total)
}

func medianWrite(writes map[string]any, name string) float64 {
	group, ok := writes[name].(map[string]any)
	if !ok {
		return 0
	}
	value, _ := group["median_ms"].(float64)
	return value
}

func equal(a, b any) bool {
	left, _ := json.Marshal(a)
	right, _ := json.Marshal(b)
	return bytes.Equal(left, right)
}
