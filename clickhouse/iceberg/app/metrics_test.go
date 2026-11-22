package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestCommandCountersDistinguishUnavailableAndZero(t *testing.T) {
	path := filepath.Join(t.TempDir(), "counter")
	if value := readCommandCounter(path); value.UnavailableError == "" || value.Raw != "" {
		t.Fatalf("missing file looked measured: %+v", value)
	}
	for _, body := range []string{"", "0\n"} {
		if err := os.WriteFile(path, []byte(body), 0600); err != nil {
			t.Fatal(err)
		}
		value := readCommandCounter(path)
		if value.Raw != body || (value.UnavailableError != "") != (body == "") {
			t.Fatalf("counter %q misclassified: %+v", body, value)
		}
	}
}

func TestCommandCPUUsesProcessDeltasAndExplicitUnavailable(t *testing.T) {
	before := commandProcessSnapshot{Usage: &commandProcessUsage{UserCoreSeconds: 2, SystemCoreSeconds: 1}}
	after := commandProcessSnapshot{Usage: &commandProcessUsage{UserCoreSeconds: 2.5, SystemCoreSeconds: 1.25}}
	cpu, err := commandCPU(before, after, time.Second)
	if err != nil || cpu.UserCoreSeconds != .5 || cpu.SystemCoreSeconds != .25 || cpu.TotalCoreSeconds != .75 || cpu.PercentOneCore100 != 75 {
		t.Fatalf("wrong process CPU delta: %+v %v", cpu, err)
	}
	for _, pair := range [][2]commandProcessSnapshot{{{}, after}, {before, {}}, {after, before}} {
		if cpu, err := commandCPU(pair[0], pair[1], time.Second); cpu != nil || err == nil {
			t.Fatalf("missing/negative counters became measured CPU: %+v %v", cpu, err)
		}
	}
	if cpu, err := commandCPU(before, after, 0); cpu != nil || err == nil {
		t.Fatal("zero-duration CPU interval was accepted")
	}
}

func TestMeasuredRunPreservesErrorsAndExclusiveReports(t *testing.T) {
	directory := t.TempDir()
	t.Setenv("RESULTS_DIR", directory)
	first := map[string][]byte{}
	for _, want := range []error{nil, errors.New("native request failed"), injectedFailure("retained hot data")} {
		calls := 0
		got := measuredRun(context.Background(), "archive", func(context.Context, string) error {
			calls++
			return want
		})
		if calls != 1 || !errors.Is(got, want) || commandExitCode(got) != commandExitCode(want) {
			t.Fatalf("workload result changed: got %v, want %v; calls=%d", got, want, calls)
		}
		files, err := filepath.Glob(filepath.Join(directory, "command-metrics-*.json"))
		if err != nil || len(files) != len(first)+1 {
			t.Fatalf("exclusive report missing: %v %v", files, err)
		}
		for _, path := range files {
			body, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			if old, exists := first[path]; exists {
				if string(old) != string(body) {
					t.Fatal("earlier report changed")
				}
				continue
			}
			var report commandMetrics
			if err := json.Unmarshal(body, &report); err != nil {
				t.Fatal(err)
			}
			if report.ExitCode != commandExitCode(want) || report.Role != "clickhouse-archive-workload" || report.Mode != "archive" || report.Invocation != filepath.Base(path) || report.StartedNS <= 0 || report.EndedNS < report.StartedNS || report.ElapsedNS < 0 || len(report.Before.Files) != len(commandCounterPaths) || len(report.After.Files) != len(commandCounterPaths) {
				t.Fatalf("incomplete report: %+v", report)
			}
			if want != nil && report.Error != want.Error() {
				t.Fatal("original failure was not recorded")
			}
			first[path] = body
		}
	}
}

func TestMetricsCreationFailurePreventsWorkload(t *testing.T) {
	t.Setenv("RESULTS_DIR", filepath.Join(t.TempDir(), "missing"))
	called := false
	err := measuredRun(context.Background(), "direct", func(context.Context, string) error {
		called = true
		return nil
	})
	if err == nil || called || commandExitCode(err) != 1 {
		t.Fatalf("workload ran without a report destination: %v, called=%v", err, called)
	}
}

func TestMetricsFinalizationFailureRetainsInjectedExit(t *testing.T) {
	parent := t.TempDir()
	directory := filepath.Join(parent, "results")
	if err := os.Mkdir(directory, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("RESULTS_DIR", directory)
	original := injectedFailure("expected injected failure")
	err := measuredRun(context.Background(), "archive", func(context.Context, string) error {
		if err := os.Rename(directory, directory+"-retained"); err != nil {
			t.Fatal(err)
		}
		return original
	})
	if !errors.Is(err, original) || commandExitCode(err) != 42 || err.Error() == original.Error() {
		t.Fatalf("finalization hid original or measurement failure: %v", err)
	}
}
