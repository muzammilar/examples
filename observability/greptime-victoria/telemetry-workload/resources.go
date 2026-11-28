package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
)

// Emit before process exit: sub-second fixture/health jobs can disappear between
// Docker samples. An unreadable counter is explicitly unavailable, never zero.
func emitResources() {
	values := make(map[string]string)
	unavailable := make(map[string]string)
	for _, name := range []string{"cpu.stat", "memory.current", "memory.peak", "io.stat"} {
		raw, err := os.ReadFile("/sys/fs/cgroup/" + name)
		if err != nil {
			unavailable[name] = err.Error()
		} else {
			values[name] = string(raw)
		}
	}
	_ = json.NewEncoder(os.Stderr).Encode(map[string]any{"event": "native-cgroup-before-exit", "values": values, "unavailable": unavailable, "scope": "whole process/container lifetime; separate from API request latency"})
}

func scrapeNativeMetrics(ctx context.Context, backend, signal string) error {
	endpoint := ""
	switch backend + ":" + signal {
	case "greptime:metrics", "greptime:logs":
		endpoint = "http://greptime:4000/metrics"
	case "victoria:metrics":
		endpoint = "http://vmetrics:8428/metrics"
	case "victoria:logs":
		endpoint = "http://vlogs:9428/metrics"
	default:
		return errors.New("invalid native metrics role")
	}
	response, err := get(ctx, endpoint)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	n, err := io.Copy(os.Stdout, io.LimitReader(response.Body, (16<<20)+1))
	if err == nil && n > 16<<20 {
		return errors.New("native metrics response exceeded its 16 MiB bound")
	}
	return err
}
