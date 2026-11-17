package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
)

type History struct {
	ACK       map[string]int     `json:"ack_versions"`
	ACKTimes  map[string]float64 `json:"ack_times"`
	Attempted map[string][]int   `json:"attempted_versions"`
}

type Tail struct {
	path     string
	file     *os.File
	pending  []byte
	ACK      map[string]int
	ACKTimes map[string]float64
	Outcomes map[string]map[int]bool
	Attempts map[string]map[int]bool
	Recent   []string
}

func loadHistory(root string) (*Tail, error) {
	data, err := os.ReadFile(filepath.Join(root, "history.json"))
	if err != nil {
		return nil, err
	}
	var history History
	if err := json.Unmarshal(data, &history); err != nil {
		return nil, err
	}
	if history.ACK == nil || history.Attempted == nil {
		return nil, fmt.Errorf("missing historical witness")
	}
	t := &Tail{path: filepath.Join(root, "writer.jsonl"), ACK: history.ACK, ACKTimes: history.ACKTimes, Attempts: make(map[string]map[int]bool), Outcomes: make(map[string]map[int]bool)}
	if t.ACKTimes == nil {
		return nil, fmt.Errorf("missing ACK timestamps")
	}
	for id, versions := range history.Attempted {
		t.Attempts[id] = make(map[int]bool)
		for _, version := range versions {
			t.Attempts[id][version] = true
		}
	}
	for id, version := range t.ACK {
		if version <= 0 || !t.Attempts[id][version] || t.ACKTimes[id] <= 0 {
			return nil, fmt.Errorf("historical ACK has no attempt: %s/%d", id, version)
		}
		t.Recent = append(t.Recent, id)
	}
	sort.Strings(t.Recent)
	return t, nil
}

func (t *Tail) consume() error {
	if t.file == nil {
		f, err := os.Open(t.path)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		t.file = f
	}
	data, err := io.ReadAll(t.file)
	if err != nil {
		return err
	}
	lines := bytes.Split(append(t.pending, data...), []byte{'\n'})
	t.pending = append([]byte(nil), lines[len(lines)-1]...)
	for _, line := range lines[:len(lines)-1] {
		var event Event
		if err := json.Unmarshal(line, &event); err != nil {
			return err
		}
		if event.Stage != "attempt" && event.Stage != "ack" && event.Stage != "ambiguous" && event.Stage != "rejected" {
			return fmt.Errorf("unknown writer stage %s", event.Stage)
		}
		for _, row := range event.Rows {
			if event.Stage == "attempt" {
				if t.Attempts[row.ID] == nil {
					t.Attempts[row.ID] = make(map[int]bool)
				}
				if t.Attempts[row.ID][row.Version] {
					return fmt.Errorf("duplicate intent %s/%d", row.ID, row.Version)
				}
				t.Attempts[row.ID][row.Version] = true
			} else {
				if !t.Attempts[row.ID][row.Version] {
					return fmt.Errorf("outcome without intent %s/%d", row.ID, row.Version)
				}
				if t.Outcomes[row.ID] == nil {
					t.Outcomes[row.ID] = make(map[int]bool)
				}
				if t.Outcomes[row.ID][row.Version] {
					return fmt.Errorf("duplicate outcome %s/%d", row.ID, row.Version)
				}
				t.Outcomes[row.ID][row.Version] = true
				if event.Stage == "ack" {
					if row.Version <= t.ACK[row.ID] {
						return fmt.Errorf("nonmonotonic ACK %s/%d", row.ID, row.Version)
					}
					t.ACK[row.ID] = row.Version
					t.ACKTimes[row.ID] = event.Time
					t.Recent = append(t.Recent, row.ID)
				}
			}
		}
	}
	if len(t.Recent) > 1000 {
		t.Recent = t.Recent[len(t.Recent)-1000:]
	}
	return nil
}

func (t *Tail) checked(f *Fixture, actual Product) error {
	expected, err := f.product(actual.ID, actual.Version)
	if err != nil {
		return err
	}
	if expected != actual {
		return fmt.Errorf("wrong inventory payload %s/%d", actual.ID, actual.Version)
	}
	if actual.Version != 0 && !t.Attempts[actual.ID][actual.Version] {
		return fmt.Errorf("version has no external intent: %s/%d", actual.ID, actual.Version)
	}
	return nil
}
