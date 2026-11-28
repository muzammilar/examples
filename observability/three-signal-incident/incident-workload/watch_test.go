package main

import "testing"

func TestRecentAnchorSurvivesIndexDelay(t *testing.T) {
	for _, result := range []string{"pending", "visibility-budget-exceeded", "rpc-fail"} {
		if advanceAnchor(result) {
			t.Fatalf("discarded unobserved ACK after %s; delayed indexes would never be sampled", result)
		}
	}
	for _, result := range []string{"visible", "fidelity-mismatch"} {
		if !advanceAnchor(result) {
			t.Fatalf("observed ACK should allow the next sample after %s", result)
		}
	}
}
