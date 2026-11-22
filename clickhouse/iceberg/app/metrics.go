package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"time"
)

var commandCounterPaths = []string{
	"/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/cpu.max",
	"/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.peak",
	"/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.swap.max",
	"/sys/fs/cgroup/io.stat", "/proc/self/io", "/proc/self/status",
}

type commandCounter struct {
	Raw              string `json:"raw,omitempty"`
	UnavailableError string `json:"unavailable_error,omitempty"`
}

type commandSnapshot struct {
	CapturedNS int64                     `json:"captured_ns"`
	Files      map[string]commandCounter `json:"files"`
}

type commandMetrics struct {
	Version             int                    `json:"version"`
	Invocation          string                 `json:"invocation"`
	Role                string                 `json:"role"`
	Mode                string                 `json:"mode"`
	StartedNS           int64                  `json:"started_ns"`
	EndedNS             int64                  `json:"ended_ns"`
	ElapsedNS           int64                  `json:"elapsed_ns"`
	ExitCode            int                    `json:"workload_exit_code"`
	Error               string                 `json:"error,omitempty"`
	ProcessBefore       commandProcessSnapshot `json:"process_before"`
	ProcessAfter        commandProcessSnapshot `json:"process_after"`
	ProcessCPU          *commandProcessCPU     `json:"process_cpu,omitempty"`
	CPUUnavailableError string                 `json:"cpu_unavailable_error,omitempty"`
	Before              commandSnapshot        `json:"before"`
	After               commandSnapshot        `json:"after"`
	Scope               string                 `json:"scope"`
}

type commandProcessSnapshot struct {
	Usage            *commandProcessUsage `json:"usage,omitempty"`
	UnavailableError string               `json:"unavailable_error,omitempty"`
}

type commandProcessUsage struct {
	UserCoreSeconds      float64 `json:"user_core_seconds"`
	SystemCoreSeconds    float64 `json:"system_core_seconds"`
	LifetimePeakRSSBytes int64   `json:"lifetime_peak_rss_bytes"`
}

type commandProcessCPU struct {
	UserCoreSeconds   float64 `json:"user_core_seconds"`
	SystemCoreSeconds float64 `json:"system_core_seconds"`
	TotalCoreSeconds  float64 `json:"total_core_seconds"`
	PercentOneCore100 float64 `json:"percent_one_core_100"`
}

func commandProcessCounters() commandProcessSnapshot {
	var usage syscall.Rusage
	if err := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); err != nil {
		return commandProcessSnapshot{UnavailableError: err.Error()}
	}
	peak := usage.Maxrss
	switch runtime.GOOS {
	case "linux":
		peak *= 1024
	case "darwin": // Darwin reports bytes; Linux reports KiB.
	default:
		return commandProcessSnapshot{UnavailableError: "unknown native maximum-RSS units"}
	}
	seconds := func(t syscall.Timeval) float64 { return float64(t.Sec) + float64(t.Usec)/1e6 }
	return commandProcessSnapshot{Usage: &commandProcessUsage{UserCoreSeconds: seconds(usage.Utime), SystemCoreSeconds: seconds(usage.Stime), LifetimePeakRSSBytes: peak}}
}

func commandCPU(before, after commandProcessSnapshot, elapsed time.Duration) (*commandProcessCPU, error) {
	if before.Usage == nil || after.Usage == nil || elapsed <= 0 {
		return nil, errors.New("native process CPU samples or positive interval unavailable")
	}
	user, system := after.Usage.UserCoreSeconds-before.Usage.UserCoreSeconds, after.Usage.SystemCoreSeconds-before.Usage.SystemCoreSeconds
	if user < 0 || system < 0 {
		return nil, errors.New("native process CPU counters moved backwards")
	}
	return &commandProcessCPU{UserCoreSeconds: user, SystemCoreSeconds: system, TotalCoreSeconds: user + system, PercentOneCore100: (user + system) / elapsed.Seconds() * 100}, nil
}

func readCommandCounter(path string) commandCounter {
	data, err := os.ReadFile(path)
	if err != nil {
		return commandCounter{UnavailableError: err.Error()}
	}
	value := commandCounter{Raw: string(data)}
	if strings.TrimSpace(value.Raw) == "" {
		value.UnavailableError = "native counter file is empty; no measurement available"
	}
	return value
}

func commandCounters() commandSnapshot {
	value := commandSnapshot{CapturedNS: time.Now().UnixNano(), Files: make(map[string]commandCounter)}
	for _, path := range commandCounterPaths {
		value.Files[path] = readCommandCounter(path)
	}
	return value
}

func commandExitCode(err error) int {
	if err == nil {
		return 0
	}
	var injected injectedFailure
	if errors.As(err, &injected) {
		return 42
	}
	return 1
}

func measuredRun(ctx context.Context, mode string, execute func(context.Context, string) error) error {
	directory := env("RESULTS_DIR", "/results")
	// Reserve an exclusive file before any mutation. A crash leaves incomplete
	// evidence, never a successful final report or an overwritten earlier run.
	file, err := os.CreateTemp(directory, "command-metrics-*.json")
	if err != nil {
		return err
	}
	defer file.Close()
	before := commandCounters()
	started := time.Now()
	processBefore := commandProcessCounters()
	workloadErr := execute(ctx, mode)
	processAfter := commandProcessCounters()
	ended := time.Now()
	cpu, cpuErr := commandCPU(processBefore, processAfter, ended.Sub(started))
	report := commandMetrics{
		Version: 1, Invocation: filepath.Base(file.Name()), Role: "clickhouse-archive-workload", Mode: mode,
		StartedNS: started.UnixNano(), EndedNS: ended.UnixNano(), ElapsedNS: ended.Sub(started).Nanoseconds(),
		ExitCode: commandExitCode(workloadErr), Before: before, After: commandCounters(),
		ProcessBefore: processBefore, ProcessAfter: processAfter, ProcessCPU: cpu,
		Scope: "Application command including setup, requests, verification, response-body cleanup and bounding getrusage calls; excludes container startup/teardown, idle HTTP pool destruction, raw-counter sampling and final report serialization/fsync. Process CPU is user/system deltas across all threads; 100% is one core. Native maximum RSS includes all process allocators, not only the Go heap. Cgroup counters/peak include all container processes and charged file cache; /proc/self describes this Go HTTP worker, not ClickHouse/storage servers. Native CPU/I/O snapshots are cumulative; memory.peak, VmHWM and maximum RSS are lifetime peaks including startup. Empty/missing counters are unavailable, not zero. rchar/wchar are syscall bytes, not physical disk bytes; io.stat is Docker-VM device accounting. workload_exit_code excludes report-persistence errors, which are returned separately and affect the real command exit. A missing/partial report does not establish completed measurements.",
	}
	if cpuErr != nil {
		report.CPUUnavailableError = cpuErr.Error()
	}
	if workloadErr != nil {
		report.Error = workloadErr.Error()
	}
	writeErr := json.NewEncoder(file).Encode(report)
	syncErr := file.Sync()
	closeErr := file.Close()
	parent, openErr := os.Open(directory)
	var directoryErr error
	if openErr == nil {
		directoryErr = errors.Join(parent.Sync(), parent.Close())
	}
	metricsErr := errors.Join(writeErr, syncErr, closeErr, openErr, directoryErr)
	if metricsErr != nil {
		metricsErr = fmt.Errorf("persist command metrics: %w", metricsErr)
	}
	return errors.Join(workloadErr, metricsErr)
}
