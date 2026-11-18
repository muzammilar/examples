package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"maps"
	"math"
	"os"
	"path/filepath"
	"sort"
)

type History struct {
	ACK       map[string]int     `json:"ack_versions"`
	ACKTimes  map[string]float64 `json:"ack_times"`
	Attempted map[string][]int   `json:"attempted_versions"`
}

type writeWindow struct {
	Row     Product
	Start   float64
	End     float64
	Outcome string
}

type Tail struct {
	Windows     map[string]map[int]*writeWindow
	PriorACK    map[string]int
	LastVersion int
	path        string
	file        *os.File
	pending     []byte
	ACK         map[string]int
	ACKTimes    map[string]float64
	Outcomes    map[string]map[int]bool
	Attempts    map[string]map[int]bool
	Recent      []string
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
	t := &Tail{Windows: make(map[string]map[int]*writeWindow), PriorACK: maps.Clone(history.ACK), path: filepath.Join(root, "writer.jsonl"), ACK: history.ACK, ACKTimes: history.ACKTimes, Attempts: make(map[string]map[int]bool), Outcomes: make(map[string]map[int]bool)}
	if t.ACKTimes == nil {
		return nil, fmt.Errorf("missing ACK timestamps")
	}
	for id, versions := range history.Attempted {
		t.Attempts[id] = make(map[int]bool)
		for _, version := range versions {
			t.Attempts[id][version] = true
			t.LastVersion = max(t.LastVersion, version)
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
		if event.Time <= 0 || math.IsNaN(event.Time) || math.IsInf(event.Time, 0) || len(event.Rows) == 0 {
			return fmt.Errorf("invalid writer timestamp or empty rows")
		}
		if event.Stage == "attempt" {
			version := event.Rows[0].Version
			if version <= t.LastVersion {
				return fmt.Errorf("non-increasing batch version")
			}
			for _, row := range event.Rows {
				if row.Version != version {
					return fmt.Errorf("mixed batch versions")
				}
			}
			t.LastVersion = version
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
				if t.Windows[row.ID] == nil {
					t.Windows[row.ID] = make(map[int]*writeWindow)
				}
				t.Windows[row.ID][row.Version] = &writeWindow{Row: row, Start: event.Time}
			} else {
				if !t.Attempts[row.ID][row.Version] {
					return fmt.Errorf("outcome without intent %s/%d", row.ID, row.Version)
				}
				window := t.Windows[row.ID][row.Version]
				if window == nil || window.Row != row {
					return fmt.Errorf("outcome lacks matching current intent")
				}
				window.Outcome = event.Stage
				if event.Stage != "ambiguous" {
					window.End = event.Time
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

// Queries can overlap attribute updates; document GETs remain atomically checked.
func (t *Tail) partialSearch(f *Fixture, actual Product, started, finished float64) (bool, error) {
	if err := t.checked(f, actual); err == nil {
		return false, nil
	}
	baseline, err := f.product(actual.ID, 0)
	if err != nil {
		return false, err
	}
	known := func(version int) bool { return version == 0 || t.Attempts[actual.ID][version] }
	if actual.Tenant != baseline.Tenant || !known(actual.Version) || !known(actual.PriceCents-baseline.PriceCents) {
		return false, fmt.Errorf("search has unknown identity/version/value: %s", actual.ID)
	}
	stockKnown := actual.Stock == baseline.Stock
	for version := range t.Attempts[actual.ID] {
		stockKnown = stockKnown || actual.Stock == version%100
	}
	if !stockKnown {
		return false, fmt.Errorf("search has unknown stock: %s", actual.ID)
	}
	for _, window := range t.Windows[actual.ID] {
		if window.Start <= finished && (window.End == 0 || window.End >= started) &&
			(actual.Version == window.Row.Version || actual.PriceCents == window.Row.PriceCents || actual.Stock == window.Row.Stock) {
			return true, nil
		}
	}
	return false, fmt.Errorf("mixed search fields without an overlapping update: %s", actual.ID)
}

func (t *Tail) searchFloor(id string, started float64) int {
	floor := t.PriorACK[id]
	for version, window := range t.Windows[id] {
		if window.Outcome == "ack" && window.End <= started {
			floor = max(floor, version)
		}
	}
	return floor
}
