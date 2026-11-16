package main

import (
	"os"
	"path/filepath"
	"testing"
)

func exampleLedger(t *testing.T, ack bool) *ledger {
	t.Helper()
	route := []int{2, 0, 1}
	r := ledgerRun{config: runConfig{Base: 100, Reserved: 4, Minimum: 4, Batch: 2, Customers: 3}, flags: make([]byte, 4)}
	for first := int64(100); first < 104; first += 2 {
		customer := int(first-100) / 2
		e := event{Stage: "attempt", First: first, Count: 2, Customer: customer, Shard: route[customer]}
		if err := r.record(e, route); err != nil {
			t.Fatal(err)
		}
		if ack {
			e.Stage = "ack"
			if err := r.record(e, route); err != nil {
				t.Fatal(err)
			}
		}
	}
	return &ledger{runs: []ledgerRun{r}, route: route}
}
func observeAll(l *ledger, r *checkReport) {
	for id := int64(100); id < 104; id++ {
		l.observe(r, id, int(id-100)/2, id%10000+1)
	}
}
func TestCompleteAcknowledgedBodies(t *testing.T) {
	l := exampleLedger(t, true)
	r := checkReport{Shard: -1}
	observeAll(l, &r)
	if err := l.finish(&r); err != nil || r.Acked != 4 || r.Missing != 0 {
		t.Fatalf("%+v %v", r, err)
	}
}
func TestEarlierAcknowledgmentsSurviveLaterRun(t *testing.T) {
	l := exampleLedger(t, true)
	newer := ledgerRun{config: runConfig{Base: 1000, Reserved: 2, Minimum: 2, Batch: 2, Customers: 3}, flags: []byte{attempted | acknowledged, attempted | acknowledged}}
	l.runs = append(l.runs, newer)
	r := checkReport{Shard: -1}
	l.observe(&r, 1000, 0, 1001)
	l.observe(&r, 1001, 0, 1002)
	if err := l.finish(&r); err == nil || r.Missing != 4 {
		t.Fatalf("old ACK loss passed: %+v", r)
	}
}
func TestCorrectBodyOnWrongShardFails(t *testing.T) {
	l := exampleLedger(t, true)
	l.owned = map[int]bool{1: true}
	r := checkReport{Shard: 1}
	l.observe(&r, 100, 0, 101)
	if err := l.finish(&r); err == nil || r.WrongShard != 1 {
		t.Fatalf("wrong placement passed: %+v", r)
	}
}
func TestBodyCorruptionAndDuplicatesFail(t *testing.T) {
	for _, defect := range []string{"customer", "amount", "duplicate", "unknown"} {
		t.Run(defect, func(t *testing.T) {
			l := exampleLedger(t, true)
			r := checkReport{Shard: -1}
			observeAll(l, &r)
			switch defect {
			case "customer":
				l.observe(&r, 100, 2, 101)
			case "amount":
				l.observe(&r, 100, 0, 999)
			case "duplicate":
				l.observe(&r, 100, 0, 101)
			case "unknown":
				l.observe(&r, 9999, 0, 10000)
			}
			if err := l.finish(&r); err == nil {
				t.Fatalf("defect passed: %+v", r)
			}
		})
	}
}
func TestAmbiguousTransactionsMustBeAtomic(t *testing.T) {
	l := exampleLedger(t, false)
	r := checkReport{Shard: -1}
	l.observe(&r, 100, 0, 101)
	if err := l.finish(&r); err == nil || r.Partial != 1 {
		t.Fatalf("partial transaction passed: %+v", r)
	}
	l = exampleLedger(t, false)
	r = checkReport{Shard: -1}
	observeAll(l, &r)
	if err := l.finish(&r); err != nil || r.Unacked != 4 {
		t.Fatalf("complete unknown commit rejected: %+v %v", r, err)
	}
}
func TestUnattemptedAllocatedIDFails(t *testing.T) {
	l := exampleLedger(t, false)
	l.runs[0].flags[0] = 0
	r := checkReport{Shard: -1}
	l.observe(&r, 100, 0, 101)
	if err := l.finish(&r); err == nil || r.Unexpected != 1 {
		t.Fatal("unattempted row passed")
	}
}
func TestAckRequiresAttempt(t *testing.T) {
	l := exampleLedger(t, false)
	l.runs[0].flags = make([]byte, 4)
	if err := l.runs[0].record(event{Stage: "ack", First: 100, Count: 2, Customer: 0, Shard: 2}, l.route); err == nil {
		t.Fatal("forged ACK passed")
	}
}
func TestTruncatedJournalIsNotSilentlyIgnored(t *testing.T) {
	path := filepath.Join(t.TempDir(), "writes.jsonl")
	if err := os.WriteFile(path, []byte("{\"stage\":\"attempt\"}\n{\"stage\":"), 0644); err != nil {
		t.Fatal(err)
	}
	if err := records(path, func(event) error { return nil }); err == nil {
		t.Fatal("truncated witness passed")
	}
}

func TestWrongLogicalShardWithinCorrectGroupFails(t *testing.T) {
	l := exampleLedger(t, true)
	l.owned = map[int]bool{0: true, 2: true}
	report := checkReport{Shard: 1}
	for id := int64(100); id < 104; id++ {
		c := int(id-100) / 2
		actual := l.route[c]
		if id == 100 {
			actual = 0
		}
		l.observePhysical(&report, id, c, id%10000+1, actual)
	}
	if err := l.finish(&report); err == nil || report.WrongShard != 1 || report.Missing != 0 {
		t.Fatalf("wrong physical shard passed: %+v", report)
	}
}
