package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"syscall"
)

func filesystemStats(path string) (map[string]any, error) {
	var stat syscall.Statfs_t
	if err := syscall.Statfs(path, &stat); err != nil {
		return nil, err
	}
	return map[string]any{"filesystem_available_bytes": uint64(stat.Bsize) * stat.Bavail,
		"filesystem_total_bytes": uint64(stat.Bsize) * stat.Blocks,
		"filesystem_type":        fmt.Sprintf("0x%x", stat.Type)}, nil
}

func filesystemSnapshot() error {
	value, err := filesystemStats("/")
	if err != nil {
		return err
	}
	value["event"] = "local-container-filesystem-capacity"
	return json.NewEncoder(os.Stdout).Encode(value)
}

func diskSnapshot() error {
	var allocated, logical, files int64
	seen := make(map[[2]uint64]bool)
	err := filepath.WalkDir("/data", func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok {
			return errors.New("filesystem block allocation unavailable")
		}
		identity := [2]uint64{uint64(stat.Dev), stat.Ino}
		if seen[identity] {
			return nil
		}
		seen[identity] = true
		allocated += stat.Blocks * 512
		if info.Mode().IsRegular() {
			logical += info.Size()
			files++
		}
		return nil
	})
	if err != nil {
		return err
	}
	capacity, err := filesystemStats("/data")
	if err != nil {
		return err
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"filesystem": capacity, "event": "data-volume-snapshot", "logical_regular_file_bytes": logical, "allocated_bytes": allocated, "regular_files": files, "scope": "read-only mounted owned data volume; inode-deduplicated; not a transient peak or native codec ratio"})
}
