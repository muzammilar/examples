package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
)

func TestCommandResourcesRetainFailureAndDistinctReports(t *testing.T) {
	directory := t.TempDir()
	want := errors.New("operation failed after submit")
	if err := measureCommand("load", directory, func() error { return want }); !errors.Is(err, want) {
		t.Fatalf("operation error changed: %v", err)
	}
	if err := measureCommand("load", directory, func() error { return nil }); err != nil {
		t.Fatal(err)
	}
	files, err := filepath.Glob(filepath.Join(directory, "command-resources-*.json"))
	if err != nil || len(files) != 2 {
		t.Fatalf("distinct reports: %v %v", files, err)
	}
	failed := 0
	for _, path := range files {
		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var report struct {
			Mode     string         `json:"mode"`
			Passed   bool           `json:"passed"`
			Error    string         `json:"error"`
			Duration int64          `json:"duration_ns"`
			Before   map[string]any `json:"native_before"`
			After    map[string]any `json:"native_after"`
		}
		if err := json.Unmarshal(data, &report); err != nil {
			t.Fatal(err)
		}
		if report.Mode != "load" || report.Duration <= 0 || report.Before["files"] == nil || report.After["files"] == nil {
			t.Fatalf("incomplete report: %+v", report)
		}
		if !report.Passed {
			failed++
			if report.Error != want.Error() {
				t.Fatalf("failure detail: %q", report.Error)
			}
		}
	}
	if failed != 1 {
		t.Fatalf("failed reports = %d", failed)
	}
}

func TestResourceReportFailureCannotHideOperationFailure(t *testing.T) {
	want := errors.New("ambiguous operation")
	missing := filepath.Join(t.TempDir(), "missing")
	err := measureCommand("load", missing, func() error { return want })
	if !errors.Is(err, want) || !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("both errors must survive: %v", err)
	}
	if err := measureCommand("verify", missing, func() error { return nil }); err == nil {
		t.Fatal("missing resource report must fail an otherwise successful command")
	}
}
