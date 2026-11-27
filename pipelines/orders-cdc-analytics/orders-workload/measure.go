package main

import (
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
)

func measureVolumes() error {
	directories, err := os.ReadDir("/storage")
	if err != nil {
		return err
	}
	values := map[string]any{}
	for _, directory := range directories {
		var apparent, allocated, files int64
		err = filepath.WalkDir(filepath.Join("/storage", directory.Name()), func(path string, entry fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if !entry.Type().IsRegular() {
				return nil
			}
			info, err := entry.Info()
			if err != nil {
				return err
			}
			native, ok := info.Sys().(*syscall.Stat_t)
			if !ok {
				return fmt.Errorf("native file allocation unavailable")
			}
			files++
			apparent += info.Size()
			allocated += native.Blocks * 512
			return nil
		})
		if err != nil {
			return err
		}
		values[directory.Name()] = map[string]any{"files": files, "apparent_bytes": apparent, "allocated_bytes": allocated}
	}
	emit(map[string]any{"event": "volume_sizes", "volumes": values, "scope": "non-atomic owned-volume snapshot, no shared-filesystem double count"})
	return nil
}
func measureCgroup() error {
	result := map[string]any{"event": "native_cgroup"}
	for _, name := range []string{"cpu.stat", "memory.peak", "memory.current", "io.stat"} {
		data, err := os.ReadFile("/sys/fs/cgroup/" + name)
		if err != nil {
			return err
		}
		result[name] = string(data)
	}
	emit(result)
	return nil
}
