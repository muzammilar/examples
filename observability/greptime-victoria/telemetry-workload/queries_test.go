package main

import "testing"

func TestGroupedOracleDistinguishesTenantsAndValues(t *testing.T) {
	f := Fixture{Series: 4, Points: 2, Logs: 8}
	counts, err := queryExpected(f, "metric-count")
	if err != nil || counts["tenant-0"] != 4 || counts["tenant-1"] != 2 || counts["tenant-2"] != 2 || len(counts) != 3 {
		t.Fatalf("counts=%v error=%v", counts, err)
	}
	sums, err := queryExpected(f, "metric-sum")
	if err != nil || sums["tenant-0"] != 4002 || sums["tenant-1"] != 2001 || sums["tenant-2"] != 6001 {
		t.Fatalf("sums=%v error=%v", sums, err)
	}
	if _, err = queryExpected(f, "unsupported"); err == nil {
		t.Fatal("unknown query accepted")
	}
}
