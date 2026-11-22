package main

import (
	"testing"
)

func TestMutablePairsCoverDisjointRowsAndPreserveBuckets(t *testing.T) {
	seen := make(map[int64]bool)
	for index := int64(0); index < 5000; index++ {
		pair := mutablePair(index)
		if pair[0]%100 != pair[1]%100 || pair[0] == pair[1] {
			t.Fatal("pair crosses bucket or reuses a row", pair)
		}
		for _, id := range pair {
			if seen[id] || id < 0 || id >= 10000 {
				t.Fatal("overlap/out-of-range pair", index, pair)
			}
			seen[id] = true
		}
	}
	if len(seen) != 10000 {
		t.Fatal("pair mapping omits seed rows")
	}
}

func TestMutableBodiesChangeButPreserveAnalyticalTotals(t *testing.T) {
	for seq := int64(1); seq <= 100; seq++ {
		left, right := mutableBody(0, seq, false), mutableBody(100, seq, true)
		if left.Amount+right.Amount != 1700 || len(left.Payload) != 256 || len(right.Payload) != 256 {
			t.Fatal("mutation broke an invariant", left, right)
		}
		if left.Payload == payload(0) || right.Payload == payload(100) || left.Revision != seq || right.Revision != seq {
			t.Fatal("mutation did not change versioned bodies")
		}
	}
	if mutableBody(0, 0, false).Payload != payload(0) || mutableBody(100, 0, true).Amount != 1700 {
		t.Fatal("initial body differs from seed")
	}
}

func TestMutableSettingsRejectUnsafeNamespaceAndReservedPair(t *testing.T) {
	t.Setenv("MUTABLE_RUN_ID", "00000000000000000000000000000000")
	t.Setenv("MUTABLE_PAIRS", "5000")
	if _, err := mutableSettings(10000); err == nil {
		t.Fatal("mutable settings allowed writes to the historical pair")
	}
	t.Setenv("MUTABLE_PAIRS", "4999")
	if _, err := mutableSettings(10000); err != nil {
		t.Fatal(err)
	}
	t.Setenv("MUTABLE_RUN_ID", "bad'; DROP TABLE orders; --")
	if _, err := mutableSettings(10000); err == nil {
		t.Fatal("invalid namespace accepted")
	}
}
