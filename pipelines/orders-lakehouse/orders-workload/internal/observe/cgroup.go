// Package observe records native counters without requiring a shell in runtime images.
package observe

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

func Values() map[string]string {
	out := map[string]string{}
	for _, name := range []string{"cpu.stat", "memory.current", "memory.peak", "memory.events", "io.stat"} {
		body, err := os.ReadFile(filepath.Join("/sys/fs/cgroup", name))
		if err != nil {
			out[name] = "unavailable: " + err.Error()
		} else {
			out[name] = string(body)
		}
	}
	return out
}
func Footer() { json.NewEncoder(os.Stdout).Encode(map[string]any{"actor_counters": Values()}) }
func Raw() {
	for name, body := range Values() {
		fmt.Printf("METRIC:%s\n%s\n", name, body)
	}
}
