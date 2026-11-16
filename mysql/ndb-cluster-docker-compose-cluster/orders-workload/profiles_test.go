package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeProfileRun(t *testing.T, root string, version, previousVersion, previousQuota int64, ack bool) {
	t.Helper()
	folder := filepath.Join(root, "profiles", time.Unix(version, 0).Format("150405"))
	if err := os.MkdirAll(folder, 0755); err != nil {
		t.Fatal(err)
	}
	body, _ := json.Marshal(profileConfig{Version: version, Operations: 10})
	if err := os.WriteFile(filepath.Join(folder, "run.json"), body, 0644); err != nil {
		t.Fatal(err)
	}
	e := event{Stage: "attempt", First: version, Count: 1, Customer: 0, Quota: previousQuota - 1, PreviousQuota: previousQuota, PreviousVersion: previousVersion}
	file, err := os.Create(filepath.Join(folder, "profiles.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err = json.NewEncoder(file).Encode(e); err != nil {
		t.Fatal(err)
	}
	if ack {
		e.Stage = "ack"
		if err = json.NewEncoder(file).Encode(e); err != nil {
			t.Fatal(err)
		}
	}
}
func TestProfileVersionGapsStillDecrementExactlyOnce(t *testing.T) {
	root := t.TempDir()
	writeProfileRun(t, root, 1, 0, 1000, true)
	writeProfileRun(t, root, 3, 1, 999, true)
	l, err := profileHistory(root, 100)
	if err != nil {
		t.Fatal(err)
	}
	v, q := l.latestACK(0)
	if v != 3 || q != 998 {
		t.Fatalf("latest acknowledged body: version=%d quota=%d", v, q)
	}
}
func TestUnknownProfileResultDoesNotReplaceAcknowledgedBody(t *testing.T) {
	root := t.TempDir()
	writeProfileRun(t, root, 1, 0, 1000, true)
	writeProfileRun(t, root, 2, 1, 999, false)
	l, err := profileHistory(root, 100)
	if err != nil {
		t.Fatal(err)
	}
	v, q := l.latestACK(0)
	if v != 1 || q != 999 || l[0][2].flags&attempted == 0 {
		t.Fatal("unknown result erased prior acknowledgement")
	}
}
func TestProfilePredecessorMustBeWitnessed(t *testing.T) {
	root := t.TempDir()
	writeProfileRun(t, root, 2, 1, 999, true)
	if _, err := profileHistory(root, 100); err == nil {
		t.Fatal("unwitnessed predecessor accepted")
	}
}
func TestProfileQuotaMustMatchPredecessor(t *testing.T) {
	root := t.TempDir()
	writeProfileRun(t, root, 1, 0, 1000, true)
	writeProfileRun(t, root, 2, 1, 998, true)
	if _, err := profileHistory(root, 100); err == nil {
		t.Fatal("quota jump accepted")
	}
}
func TestTwentyScanSamplesUseNearestRankTail(t *testing.T) {
	values := make([]float64, 20)
	for i := range values {
		values[i] = float64(i + 1)
	}
	r := subscriberStats(values, time.Second)
	if r.P99 != 20 || r.P50 != 10 || len(r.Samples) != 20 {
		t.Fatalf("incorrect small-sample percentile: %+v", r)
	}
}
