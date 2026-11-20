package main

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

func change(offset int64, op, name string) *kgo.Record {
	body, _ := json.Marshal(map[string]any{"op": op, "after": map[string]any{"id": "customer-0", "display_name": name, "segment": "gold", "cdc_probe": offset}})
	return &kgo.Record{Topic: "customers", Partition: 0, Offset: offset, Key: []byte(`{"id":"customer-0"}`), Value: body}
}
func TestContinuesAfterReadiness(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	state := newCustomerState()
	ready := make(chan struct{})
	advance := make(chan struct{})
	updated := make(chan struct{})
	done := make(chan error, 1)
	calls := 0
	poll := func(ctx context.Context) kgo.Fetches {
		var record *kgo.Record
		switch calls {
		case 0:
			record = change(0, "r", "old")
		case 1:
			select {
			case <-advance:
			case <-ctx.Done():
				return nil
			}
			record = change(1, "u", "new")
		default:
			close(updated)
			<-ctx.Done()
			return nil
		}
		calls++
		return kgo.Fetches{{Topics: []kgo.FetchTopic{{Topic: "customers", Partitions: []kgo.FetchPartition{{Partition: 0, Records: []*kgo.Record{record}}}}}}}
	}
	go func() { done <- consumeCustomers(ctx, poll, state, 1, ready) }()
	select {
	case <-ready:
	case <-ctx.Done():
		t.Fatal("bootstrap did not become ready")
	}
	close(advance)
	select {
	case <-updated:
	case <-ctx.Done():
		t.Fatal("CDC stopped at readiness")
	}
	value, ok := state.get("customer-0")
	if !ok || value.DisplayName != "new" {
		t.Fatalf("stale cache: %v", value)
	}
	value.DisplayName = "caller mutation"
	value, _ = state.get("customer-0")
	if value.DisplayName != "new" {
		t.Fatal("cache exposed mutable protobuf state")
	}
	count, applied, probe, positions := state.snapshot()
	if count != 1 || applied != 2 || probe != 1 || positions[0] != 2 {
		t.Fatal("incorrect cache progress")
	}
	positions[0] = 999
	_, _, _, positions = state.snapshot()
	if positions[0] != 2 {
		t.Fatal("progress snapshot aliases cache")
	}
	cancel()
	if err := <-done; !errors.Is(err, context.Canceled) {
		t.Fatal(err)
	}
}
func TestCDCDeleteTombstoneAndInvalidBody(t *testing.T) {
	state := newCustomerState()
	if err := state.apply(change(0, "r", "initial")); err != nil {
		t.Fatal(err)
	}
	bad := change(1, "u", "invalid")
	bad.Key = []byte(`{"id":"other"}`)
	if err := state.apply(bad); err == nil {
		t.Fatal("accepted mismatched CDC key")
	}
	unknown := change(1, "?", "invalid")
	if err := state.apply(unknown); err == nil {
		t.Fatal("accepted unknown CDC operation")
	}
	if err := state.apply(&kgo.Record{Partition: 0, Offset: 1, Key: []byte(`{"id":"customer-0"}`), Value: []byte(`{"op":"d","after":null}`)}); err != nil {
		t.Fatal(err)
	}
	if _, ok := state.get("customer-0"); ok {
		t.Fatal("delete retained customer")
	}
	tombstone := &kgo.Record{Partition: 0, Offset: 2, Key: []byte(`{"payload":{"id":"customer-0"}}`)}
	if err := state.apply(tombstone); err != nil {
		t.Fatal(err)
	}
	if err := state.apply(change(0, "r", "old replay")); err != nil {
		t.Fatal(err)
	}
	if _, ok := state.get("customer-0"); ok {
		t.Fatal("old offset resurrected deleted customer")
	}
}
