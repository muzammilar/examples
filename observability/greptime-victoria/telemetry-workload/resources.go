package main

import (
	"encoding/json"
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
