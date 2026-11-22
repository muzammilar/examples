package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"runtime"
	"syscall"
	"time"
)

// Counters are saved before exit because Compose removes one-shot containers.
// The operation returns only after its deferred engine/connection closes run.
func measureCommand(mode, directory string, operation func() error) error {
	started := time.Now()
	before := recoveryNativeCounters()
	var startCPU, endCPU syscall.Rusage
	startErr := syscall.Getrusage(syscall.RUSAGE_SELF, &startCPU)
	operationErr := operation()
	endErr := syscall.Getrusage(syscall.RUSAGE_SELF, &endCPU)
	after := recoveryNativeCounters()
	ended := time.Now()
	errorText := ""
	if operationErr != nil {
		errorText = operationErr.Error()
	}
	report := map[string]any{
		"mode": mode, "passed": operationErr == nil, "error": errorText,
		"started_ns": started.UnixNano(), "ended_ns": ended.UnixNano(),
		"duration_ns":   ended.Sub(started).Nanoseconds(),
		"native_before": before, "native_after": after,
		"scope": "Application dispatch including engine setup, execution, validation and close, plus counter probes; excludes process startup and report write/fsync. Raw native counters include all engine threads and cgroup cache/kernel charges. Peaks are lifetime values, not deltas. Empty/missing I/O counters are unavailable, not zero; process rchar/wchar are not physical disk bytes. Server resource costs are separate.",
	}
	if startErr == nil && endErr == nil {
		user := float64(endCPU.Utime.Nano()-startCPU.Utime.Nano()) / 1e9
		system := float64(endCPU.Stime.Nano()-startCPU.Stime.Nano()) / 1e9
		report["process_cpu"] = map[string]float64{
			"user_core_seconds": user, "system_core_seconds": system,
			"utilization_percent_one_core_100": (user + system) / ended.Sub(started).Seconds() * 100,
		}
		rss := endCPU.Maxrss
		if runtime.GOOS == "linux" {
			rss *= 1024
		}
		report["process_lifetime_peak_rss_bytes"] = rss
	} else {
		report["process_cpu_unavailable"] = fmt.Sprintf("start=%v end=%v", startErr, endErr)
	}
	if err := saveCommandMetrics(directory, report); err != nil {
		return errors.Join(operationErr, fmt.Errorf("save command resource report: %w", err))
	}
	return operationErr
}

func saveCommandMetrics(directory string, report map[string]any) (err error) {
	file, err := os.CreateTemp(directory, "command-resources-*.json")
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	encoder := json.NewEncoder(file)
	encoder.SetIndent("", "  ")
	if err = encoder.Encode(report); err != nil {
		return err
	}
	if err = file.Sync(); err != nil {
		return err
	}
	dir, err := os.Open(directory)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, dir.Close()) }()
	return dir.Sync()
}
