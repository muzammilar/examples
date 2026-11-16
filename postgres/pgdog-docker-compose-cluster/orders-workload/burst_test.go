package main

import "testing"

func TestBurstIncludesEarlierAcknowledgements(t *testing.T) {
	l := exampleLedger(t, true)
	l.runs = append(l.runs, ledgerRun{config: runConfig{Base: 1000, Batch: 2, Customers: 3}, flags: []byte{attempted | acknowledged, attempted | acknowledged, attempted}})
	totals := customerTotals(l, 3)
	if totals[0] != 2206 || totals[1] != 207 || totals[2] != 0 {
		t.Fatalf("historical totals wrong: %v", totals)
	}
}
