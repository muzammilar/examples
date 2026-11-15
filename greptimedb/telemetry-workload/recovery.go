package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
)

// Reconcile all previous experiments too: a later failure must not erase rows
// acknowledged in an earlier test. Unacknowledged attempts are optional outcomes.
func verifyRecoveryHistory() {
	paths, e := filepath.Glob(filepath.Join(results, "fault-*.jsonl"))
	check(e)
	if len(paths) == 0 {
		return
	}
	attempted := map[int64]bool{}
	acked := map[int64]bool{}
	for _, path := range paths {
		file, e := os.Open(path)
		check(e)
		scan := bufio.NewScanner(file)
		for scan.Scan() {
			var r struct {
				Stage string `json:"stage"`
				First int64  `json:"first"`
				Count int64  `json:"count"`
			}
			check(json.Unmarshal(scan.Bytes(), &r))
			for j := int64(0); j < r.Count; j++ {
				key := r.First + j
				if r.Stage == "attempt" {
					attempted[key] = true
				}
				if r.Stage == "ack" {
					acked[key] = true
				}
			}
		}
		check(scan.Err())
		check(file.Close())
	}
	if len(acked) == 0 {
		panic("fault history contains no acknowledged rows")
	}
	seen := map[int64]bool{}
	for _, row := range sql("SELECT event_id,customer_id,measurement FROM recovery_events") {
		key := int64(row[0].(float64))
		if !attempted[key] || seen[key] || int64(row[1].(float64)) != key%10000 || int64(row[2].(float64)) != key*3 {
			panic(fmt.Sprintf("unexpected, duplicate or corrupt recovery row %d", key))
		}
		seen[key] = true
	}
	for key := range acked {
		if !seen[key] {
			panic(fmt.Sprintf("lost historical acknowledged row %d", key))
		}
	}
	save("recovery-history", map[string]any{"cases": len(paths), "acknowledged_rows": len(acked), "observed_attempted_rows": len(seen), "missing_acknowledged": 0, "unexpected_or_duplicate": 0, "wrong_values": 0})
	fmt.Println("Historical recovery rows", len(acked), "ACKs verified across", len(paths), "cases")
}
