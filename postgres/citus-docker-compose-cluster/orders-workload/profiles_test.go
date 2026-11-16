package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestProfileHistoryKeepsEarlierACKAndRejectsForgedACK(t *testing.T) {
	root := t.TempDir()
	for version := int64(1); version <= 2; version++ {
		dir := filepath.Join(root, "analytics", profileName(version, 0))
		if err := os.MkdirAll(dir, 0755); err != nil {
			t.Fatal(err)
		}
		data, _ := json.Marshal(profileConfig{Version: version, Requests: 5})
		if err := os.WriteFile(filepath.Join(dir, "analytics.json"), data, 0644); err != nil {
			t.Fatal(err)
		}
		e := event{Stage: "attempt", First: version, Customer: 0, Count: 1, Shard: 10}
		data, _ = json.Marshal(e)
		data = append(data, '\n')
		if version == 1 {
			e.Stage = "ack"
			ack, _ := json.Marshal(e)
			data = append(data, append(ack, '\n')...)
		}
		if err := os.WriteFile(filepath.Join(dir, "profiles.jsonl"), data, 0644); err != nil {
			t.Fatal(err)
		}
	}
	l, err := profileHistory(root, []int{10, 11, 12, 13, 14})
	if err != nil || l.latestACK(0) != 1 || l[0][2]&attempted == 0 {
		t.Fatalf("earlier ACK hidden: %v %v", l, err)
	}
	path := filepath.Join(root, "analytics", profileName(2, 0), "profiles.jsonl")
	data, _ := json.Marshal(event{Stage: "ack", First: 2, Customer: 0, Count: 1, Shard: 10})
	if err := os.WriteFile(path, append(data, '\n'), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := profileHistory(root, []int{10, 11, 12, 13, 14}); err == nil {
		t.Fatal("forged profile ACK passed")
	}
}
