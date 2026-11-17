package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type Event struct {
	Stage     string    `json:"stage"`
	Time      float64   `json:"time"`
	Seconds   float64   `json:"seconds,omitempty"`
	Rows      []Product `json:"rows,omitempty"`
	Server    string    `json:"server,omitempty"`
	Error     string    `json:"error,omitempty"`
	Mode      string    `json:"mode,omitempty"`
	Tenant    int       `json:"tenant,omitempty"`
	Hits      int       `json:"hits,omitempty"`
	Attempts  int       `json:"attempts,omitempty"`
	ID        string    `json:"id,omitempty"`
	Expected  int       `json:"expected_version,omitempty"`
	Observed  int       `json:"observed_version,omitempty"`
	RequestID string    `json:"request_id,omitempty"`
	ACKAge    float64   `json:"ack_age_seconds,omitempty"`
}

type Journal struct{ file *os.File }

func openJournal(path string) (*Journal, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return nil, err
	}
	return &Journal{file: f}, nil
}

func (j *Journal) append(event Event) error {
	event.Time = float64(time.Now().UnixNano()) / 1e9
	if err := json.NewEncoder(j.file).Encode(event); err != nil {
		return err
	}
	return j.file.Sync()
}

func saveJSON(path string, value any) error {
	f, err := os.CreateTemp(filepath.Dir(path), ".summary-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if err := json.NewEncoder(f).Encode(value); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}

func stopRequested(root, role string) (bool, error) {
	_, err := os.Stat(filepath.Join(root, "stop-"+role))
	if os.IsNotExist(err) {
		return false, nil
	}
	return err == nil, err
}

func boundedInteger(name string, fallback, low, high int) (int, error) {
	value := fallback
	if text := os.Getenv(name); text != "" {
		var err error
		if value, err = strconv.Atoi(text); err != nil {
			return 0, fmt.Errorf("%s: %w", name, err)
		}
	}
	if value < low || value > high {
		return 0, fmt.Errorf("%s must be %d..%d", name, low, high)
	}
	return value, nil
}
