package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
)

type delivery uint8

const (
	unattempted delivery = iota
	ambiguous
	acknowledged
)

type reconciliation struct {
	Acknowledged        int   `json:"acknowledged_records"`
	AcknowledgedPresent int   `json:"acknowledged_present"`
	MissingAcknowledged []int `json:"missing_acknowledged_ids"`
	Ambiguous           int   `json:"ambiguous_records"`
	AmbiguousPresent    int   `json:"ambiguous_present"`
	AmbiguousAbsent     int   `json:"ambiguous_absent_at_cut"`
	Unexpected          []int `json:"unexpected_ids"`
	DuplicateCopies     int   `json:"duplicate_copies_returned_by_query"`
}

type history struct {
	states []delivery
	digest string
}

func readHistory(path, signal string, total int) (*history, error) {
	factor := 1
	if signal == "traces" {
		factor = 3
	}
	result := &history{states: make([]delivery, total*factor)}
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	hash := sha256.New()
	decoder := json.NewDecoder(io.TeeReader(file, hash))
	next := 0
	var pending *RequestRecord
	for {
		var row RequestRecord
		err = decoder.Decode(&row)
		if err == io.EOF {
			break
		} else if err != nil {
			return nil, err
		}
		if row.Signal != signal || row.First < 0 || row.Last <= row.First || row.Last > total {
			return nil, errors.New("history signal/range differs from fixture")
		}
		switch row.Event {
		case "intent":
			if pending != nil || row.First != next {
				return nil, errors.New("missing/overlapping/concurrent witness intent")
			}
			for id := row.First * factor; id < row.Last*factor; id++ {
				result.states[id] = ambiguous
			}
			pending = &row
			next = row.Last
		case "response":
			if pending == nil || row.First != pending.First || row.Last != pending.Last || row.RawSHA256 != pending.RawSHA256 {
				return nil, errors.New("response differs from durable intent")
			}
			if row.Outcome != "accepted" && row.Outcome != "unacknowledged" {
				return nil, errors.New("unknown witness outcome")
			}
			if row.Outcome == "accepted" {
				for id := row.First * factor; id < row.Last*factor; id++ {
					result.states[id] = acknowledged
				}
			}
			pending = nil
		default:
			return nil, fmt.Errorf("unknown witness event %q", row.Event)
		}
	}
	result.digest = hex.EncodeToString(hash.Sum(nil))
	return result, nil
}

func (h *history) reconcile(seen []int) (reconciliation, error) {
	result := reconciliation{MissingAcknowledged: []int{}, Unexpected: []int{}}
	if len(seen) != len(h.states) {
		return result, errors.New("history/readback size differs")
	}
	for id, state := range h.states {
		if seen[id] > 1 {
			result.DuplicateCopies += seen[id] - 1
		}
		switch state {
		case acknowledged:
			result.Acknowledged++
			if seen[id] > 0 {
				result.AcknowledgedPresent++
			} else {
				result.MissingAcknowledged = append(result.MissingAcknowledged, id)
			}
		case ambiguous:
			result.Ambiguous++
			if seen[id] > 0 {
				result.AmbiguousPresent++
			} else {
				result.AmbiguousAbsent++
			}
		default:
			if seen[id] > 0 {
				result.Unexpected = append(result.Unexpected, id)
			}
		}
	}
	if len(result.Unexpected) > 0 {
		return result, errors.New("query returned unattempted logical IDs")
	}
	return result, nil
}
