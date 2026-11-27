package main

import (
	"fmt"
	"strings"
	"testing"
)

func TestWitnessRejectsFalseAcknowledgements(t *testing.T) {
	run := strings.Repeat("a", 32)
	record := func(event string) string {
		return fmt.Sprintf(`{"run":%q,"operation_id":%q,"operation":"seed-orders","event":%q,"first_id":1,"last_id":100,"affected":100}`+"\n", run, run+"-seed-1", event)
	}
	good := record("intent") + record("ack")
	for name, data := range map[string]string{"ack-without-intent": record("ack"), "duplicate-ack": good + record("ack"), "duplicate-intent": record("intent") + record("intent"), "truncated": strings.TrimSuffix(good, "\n"), "foreign-run": strings.ReplaceAll(good, run, strings.Repeat("b", 32)), "changed-range": record("intent") + strings.ReplaceAll(record("ack"), `"last_id":100`, `"last_id":101`)} {
		t.Run(name, func(t *testing.T) {
			if _, err := parseWitness([]byte(data), run); err == nil {
				t.Fatal("invalid history admitted")
			}
		})
	}
	history, err := parseWitness([]byte(good), run)
	if err != nil || len(history) != 1 || history[0].outcome.Event != "ack" {
		t.Fatalf("valid history rejected: %v", err)
	}
	incomplete, err := parseWitness([]byte(record("intent")), run)
	if err != nil || incomplete[0].outcome != nil {
		t.Fatalf("incomplete intent lost ambiguity: %v", err)
	}
}

func TestHistoricalOracleSelectsExactUntouchedKeys(t *testing.T) {
	for _, shape := range []struct {
		orders, customers int
		first, last       int64
	}{{10000, 100, 9051, 9164}, {1000000, 10000, 905001, 905064}} {
		rows := historicalRows(shape.orders, shape.customers)
		if len(rows) != 64 || rows[0].ID != shape.first || rows[63].ID != shape.last {
			t.Fatalf("wrong historical keys: %+v", shape)
		}
		for index, row := range rows {
			if row.ID <= int64(shape.orders/10) || row.Customer <= int64(shape.customers/2) || (index > 0 && row.ID <= rows[index-1].ID) {
				t.Fatal("historical oracle overlaps mutations or duplicates keys")
			}
		}
	}
}
