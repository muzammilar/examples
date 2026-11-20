package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"sync"

	demov1 "example.com/kafka-enricher/kafka/demo/v1"
	"github.com/twmb/franz-go/pkg/kgo"
	"google.golang.org/protobuf/proto"
)

type customerState struct {
	mu        sync.RWMutex
	byID      map[string]*demov1.Customer
	positions map[int32]int64
	applied   uint64
	probe     int64
}

func newCustomerState() *customerState {
	return &customerState{byID: make(map[string]*demov1.Customer), positions: make(map[int32]int64)}
}

type cdcRecord struct {
	After *struct {
		ID          string `json:"id"`
		DisplayName string `json:"display_name"`
		Segment     string `json:"segment"`
		Probe       int64  `json:"cdc_probe"`
	} `json:"after"`
	Op string `json:"op"`
}

func unwrapConnectPayload(raw json.RawMessage) json.RawMessage {
	var message struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &message); err == nil && message.Payload != nil {
		return message.Payload
	}
	return raw
}

func (s *customerState) apply(record *kgo.Record) error {
	var key struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(unwrapConnectPayload(record.Key), &key); err != nil {
		return fmt.Errorf("decode CDC key: %w", err)
	}
	if key.ID == "" || record.Partition < 0 || record.Offset < 0 {
		return errors.New("invalid CDC identity/position")
	}
	var change cdcRecord
	var customer *demov1.Customer
	if record.Value != nil {
		if err := json.Unmarshal(unwrapConnectPayload(record.Value), &change); err != nil {
			return fmt.Errorf("decode CDC envelope: %w", err)
		}
		switch change.Op {
		case "r", "c", "u":
			if change.After == nil || change.After.ID != key.ID || change.After.DisplayName == "" {
				return errors.New("invalid CDC customer body")
			}
			switch change.After.Segment {
			case "gold", "silver", "standard":
			default:
				return errors.New("invalid customer segment")
			}
			customer = &demov1.Customer{CustomerId: key.ID, DisplayName: change.After.DisplayName, Segment: change.After.Segment}
		case "d":
		default:
			return fmt.Errorf("unsupported CDC operation %q", change.Op)
		}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if record.Offset < s.positions[record.Partition] {
		return nil
	}
	if customer == nil {
		delete(s.byID, key.ID)
	} else {
		s.byID[key.ID] = customer
		if key.ID == "customer-0" && change.After.Probe > s.probe {
			s.probe = change.After.Probe
		}
	}
	s.positions[record.Partition] = record.Offset + 1
	s.applied++
	return nil
}

func (s *customerState) get(id string) (*demov1.Customer, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	value, ok := s.byID[id]
	if !ok {
		return nil, false
	}
	return proto.Clone(value).(*demov1.Customer), true
}

func (s *customerState) snapshot() (int, uint64, int64, map[int32]int64) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return len(s.byID), s.applied, s.probe, maps.Clone(s.positions)
}

// Readiness marks the configured seed population; streaming must continue afterward.
// Cache state is rebuilt from the compacted topic on every process restart.
func consumeCustomers(ctx context.Context, poll func(context.Context) kgo.Fetches, s *customerState, expected int, ready chan<- struct{}) error {
	initialized := false
	for {
		fetches := poll(ctx)
		if ctx.Err() != nil {
			return context.Cause(ctx)
		}
		if errs := fetches.Errors(); len(errs) > 0 {
			return fmt.Errorf("customer CDC fetch: %w", errs[0].Err)
		}
		var applyErr error
		fetches.EachRecord(func(record *kgo.Record) {
			if applyErr == nil {
				applyErr = s.apply(record)
			}
		})
		if applyErr != nil {
			return applyErr
		}
		count, _, _, _ := s.snapshot()
		if !initialized && count >= expected {
			close(ready)
			initialized = true
		}
	}
}
