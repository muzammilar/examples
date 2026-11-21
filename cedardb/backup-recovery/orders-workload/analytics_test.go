package main

import "testing"

func TestAggregateVerifierRejectsWrongGroupAndPartialOutput(t *testing.T) {
	expected := expectedGroups(10000)
	if err := checkGroups(expected, expected); err != nil {
		t.Fatal(err)
	}
	if err := checkGroups(expected[:99], expected); err == nil {
		t.Fatal("accepted missing group")
	}
	wrong := append([]aggregate(nil), expected...)
	wrong[0].Amount++
	wrong[1].Amount-- // Preserve the grand total: totals alone miss this corruption.
	if err := checkGroups(wrong, expected); err == nil {
		t.Fatal("accepted incorrect groups with a matching total")
	}
	if expected[0].Count != 100 || expected[0].Bytes != 25600 {
		t.Fatal("unexpected seed aggregation")
	}
}
