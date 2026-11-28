package main

import (
	"encoding/json"
	"os"
	"time"
)

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
