package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

type history struct {
	acks    map[string]int
	intents map[string]map[int]Product
	file    *os.File
	offset  int64
	recent  string
}

func newHistory(root string) (*history, error) {
	h := &history{acks: make(map[string]int), intents: make(map[string]map[int]Product)}
	data, err := os.ReadFile(filepath.Join(root, "history.json"))
	if err != nil {
		return nil, err
	}
	var paths []string
	if err = json.Unmarshal(data, &paths); err != nil {
		return nil, err
	}
	for _, path := range paths {
		f, err := os.Open(filepath.Join("/history", path, "writer.jsonl"))
		if err != nil {
			return nil, err
		}
		err = h.consumeFile(f, true)
		f.Close()
		if err != nil {
			return nil, err
		}
	}
	return h, nil
}
func (h *history) consumeFile(f *os.File, complete bool) error {
	scanner := bufio.NewReader(f)
	for {
		line, err := scanner.ReadString('\n')
		if err == io.EOF {
			if complete && len(line) > 0 {
				return fmt.Errorf("truncated historical witness")
			}
			return nil
		}
		if err != nil {
			return err
		}
		var event Event
		if err = json.Unmarshal([]byte(line), &event); err != nil {
			return err
		}
		if event.Stage != "attempt" && event.Stage != "ack" && event.Stage != "ambiguous" {
			return fmt.Errorf("unknown writer stage %q", event.Stage)
		}
		for _, row := range event.Rows {
			if event.Stage == "attempt" {
				h.recent = row.ID
				if h.intents[row.ID] == nil {
					h.intents[row.ID] = make(map[int]Product)
				}
				h.intents[row.ID][row.Version] = row
			}
			if event.Stage == "ack" {
				if h.intents[row.ID][row.Version] != row {
					return fmt.Errorf("ACK without identical intent")
				}
				h.acks[row.ID] = max(h.acks[row.ID], row.Version)
			}
		}
		if !complete {
			h.offset += int64(len(line))
		}
	}
}
func (h *history) consume(root string) error {
	if h.file == nil {
		f, err := os.Open(filepath.Join(root, "writer.jsonl"))
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		h.file = f
	}
	if _, err := h.file.Seek(h.offset, io.SeekStart); err != nil {
		return err
	}
	return h.consumeFile(h.file, false)
}
func (h *history) check(f *Fixture, p Product, floor int) error {
	expected, err := f.product(p.ID, p.Version)
	if err != nil {
		return err
	}
	if p != expected {
		return fmt.Errorf("wrong inventory body: %+v expected %+v", p, expected)
	}
	if p.Version > 0 && h.intents[p.ID][p.Version] != p {
		return fmt.Errorf("version without witnessed intent: %+v", p)
	}
	if p.Version < floor {
		return fmt.Errorf("stale primary read %s: version %d below acknowledged %d", p.ID, p.Version, floor)
	}
	return nil
}
