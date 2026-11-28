package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sort"
	"time"
)

type deliveryState uint8

const (
	notAttempted deliveryState = iota
	ambiguous
	acknowledged
)

type deliveryHistory struct {
	states   []deliveryState
	requests int
	digest   string
	accepted []RequestRecord
}

type reconciliation struct {
	Acknowledged        int   `json:"acknowledged_records"`
	AcknowledgedPresent int   `json:"acknowledged_present"`
	MissingAcknowledged []int `json:"missing_acknowledged_ids"`
	Ambiguous           int   `json:"ambiguous_records"`
	AmbiguousPresent    int   `json:"ambiguous_present"`
	AmbiguousAbsent     int   `json:"ambiguous_absent_at_cut"`
	Unexpected          []int `json:"unexpected_ids"`
}

func readHistory(path string, limit int) (*deliveryHistory, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	hash := sha256.New()
	history, err := decodeHistory(io.TeeReader(file, hash), limit)
	if err != nil {
		return nil, err
	}
	history.digest = hex.EncodeToString(hash.Sum(nil))
	return history, nil
}

func decodeHistory(source io.Reader, limit int) (*deliveryHistory, error) {
	history := &deliveryHistory{states: make([]deliveryState, limit)}
	decoder := json.NewDecoder(source)
	next := 0
	var pending *RequestRecord
	for {
		var row RequestRecord
		err := decoder.Decode(&row)
		if err == io.EOF {
			return history, nil
		}
		if err != nil {
			return nil, err
		}
		if row.First < 0 || row.Last <= row.First || row.Last > limit {
			return nil, errors.New("history range outside fixture")
		}
		switch row.Event {
		case "intent":
			if pending != nil || row.First != next {
				return nil, errors.New("overlapping, missing or concurrent history intent")
			}
			for i := row.First; i < row.Last; i++ {
				history.states[i] = ambiguous
			}
			next = row.Last
			history.requests++
			copy := row
			pending = &copy
		case "response":
			if pending == nil || pending.First != row.First || pending.Last != row.Last || pending.BodySHA256 != row.BodySHA256 || pending.Signal != row.Signal {
				return nil, errors.New("response differs from durable intent")
			}
			switch row.Outcome {
			case "accepted":
				history.accepted = append(history.accepted, row)
				for i := row.First; i < row.Last; i++ {
					history.states[i] = acknowledged
				}
			case "unacknowledged", "partial-acceptance", "ambiguous-or-rejected":
			default:
				return nil, fmt.Errorf("unknown response outcome %q", row.Outcome)
			}
			pending = nil
		default:
			return nil, fmt.Errorf("unknown history event %q", row.Event)
		}
	}
}

func (h *deliveryHistory) reconcile(seen []bool) (reconciliation, error) {
	result := reconciliation{MissingAcknowledged: []int{}, Unexpected: []int{}}
	if len(seen) != len(h.states) {
		return result, errors.New("readback extent differs from fixture")
	}
	for id, state := range h.states {
		switch state {
		case acknowledged:
			result.Acknowledged++
			if seen[id] {
				result.AcknowledgedPresent++
			} else {
				result.MissingAcknowledged = append(result.MissingAcknowledged, id)
			}
		case ambiguous:
			result.Ambiguous++
			if seen[id] {
				result.AmbiguousPresent++
			} else {
				result.AmbiguousAbsent++
			}
		case notAttempted:
			if seen[id] {
				result.Unexpected = append(result.Unexpected, id)
			}
		}
	}
	if len(result.Unexpected) > 0 {
		return result, errors.New("records exist without a witnessed request")
	}
	return result, nil
}

func allAcknowledged(limit int) *deliveryHistory {
	history := &deliveryHistory{states: make([]deliveryState, limit)}
	for id := range history.states {
		history.states[id] = acknowledged
	}
	return history
}

func (h *deliveryHistory) acknowledgedBefore(id int, cut time.Time) bool {
	position := sort.Search(len(h.accepted), func(i int) bool { return h.accepted[i].Last > id })
	if position == len(h.accepted) {
		return false
	}
	row := h.accepted[position]
	return row.First <= id && !row.EndedAt.IsZero() && row.EndedAt.Before(cut)
}
