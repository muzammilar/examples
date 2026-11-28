package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"regexp"
	"strings"
	"time"
)

var casePattern = regexp.MustCompile(`^[a-f0-9]{32}$`)

func witnessPayload(run string, sequence int) string {
	sum := sha256.Sum256([]byte(fmt.Sprintf("orders-control:%s:%d", run, sequence)))
	return hex.EncodeToString(sum[:])
}
func writeWitness(ctx context.Context, db *sql.DB, run string) error {
	file, err := os.OpenFile("/results/control-writes-"+run+".jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	appendEvent := func(row map[string]any) error {
		row["case"] = run
		row["utc_ns"] = time.Now().UnixNano()
		if e := json.NewEncoder(file).Encode(row); e != nil {
			return e
		}
		return file.Sync()
	}
	for batch := 0; batch < 256; batch++ {
		first := batch*8 + 1
		row := map[string]any{"event": "intent", "first": first, "last": first + 7}
		if err = appendEvent(row); err != nil {
			return err
		}
		values := make([]string, 8)
		for i := range values {
			seq := first + i
			values[i] = fmt.Sprintf("('%s',%d,'%s')", run, seq, witnessPayload(run, seq))
		}
		started := time.Now()
		_, err = db.ExecContext(ctx, "INSERT INTO pipeline.write_witness SETTINGS insert_quorum=2,insert_deduplicate=0 VALUES "+strings.Join(values, ","))
		row["duration_ms"] = float64(time.Since(started).Nanoseconds()) / 1e6
		row["event"] = "ack"
		if err != nil {
			row["event"] = "ambiguous"
			row["error"] = err.Error()
		}
		if e := appendEvent(row); e != nil {
			return e
		}
		emit(row)
		if err != nil {
			return err
		}
		time.Sleep(200 * time.Millisecond)
	}
	return nil
}
func verifyWitness(ctx context.Context, db *sql.DB, run string) error {
	bytes, err := os.ReadFile("/results/control-writes-" + run + ".jsonl")
	if err != nil {
		return err
	}
	// Bounded writer has 512 records, no opaque or silently truncated histories.
	if len(bytes) == 0 || bytes[len(bytes)-1] != '\n' {
		return fmt.Errorf("truncated witness")
	}
	intents := map[int]string{}
	outcomes := map[int]string{}
	for _, line := range strings.Split(strings.TrimSuffix(string(bytes), "\n"), "\n") {
		var record struct {
			Event       string
			First, Last int
			Case        string
		}
		if err = json.Unmarshal([]byte(line), &record); err != nil {
			return err
		}
		if record.Case != run || record.First < 1 || (record.First-1)%8 != 0 || record.Last != record.First+7 || record.Last > 2048 {
			return fmt.Errorf("invalid witness record")
		}
		if record.Event == "intent" {
			if _, ok := intents[record.First]; ok {
				return fmt.Errorf("duplicate intent")
			}
			intents[record.First] = "intent"
		} else {
			if _, ok := intents[record.First]; !ok {
				return fmt.Errorf("missing intent")
			}
			if _, ok := outcomes[record.First]; ok {
				return fmt.Errorf("duplicate outcome")
			}
			if record.Event != "ack" && record.Event != "ambiguous" {
				return fmt.Errorf("invalid outcome")
			}
			outcomes[record.First] = record.Event
		}
	}
	rows, err := db.QueryContext(ctx, "SELECT sequence,payload FROM pipeline.write_witness WHERE run='"+run+"' ORDER BY sequence LIMIT 2049")
	if err != nil {
		return err
	}
	defer rows.Close()
	present := map[int]bool{}
	for rows.Next() {
		var sequence int
		var payload string
		if err = rows.Scan(&sequence, &payload); err != nil {
			return err
		}
		first := (sequence-1)/8*8 + 1
		if _, ok := intents[first]; !ok || sequence < 1 || sequence > 2048 || present[sequence] || payload != witnessPayload(run, sequence) {
			return fmt.Errorf("wrong/duplicate/unwitnessed successful body %d", sequence)
		}
		present[sequence] = true
	}
	if err = rows.Err(); err != nil {
		return err
	}
	ack := 0
	ambiguousPresent, ambiguousAbsent := 0, 0
	for first := range intents {
		count := 0
		for seq := first; seq < first+8; seq++ {
			if present[seq] {
				count++
			}
		}
		if outcomes[first] == "ack" {
			if count != 8 {
				return fmt.Errorf("missing ACK batch %d", first)
			}
			ack++
		} else {
			if count != 0 && count != 8 {
				return fmt.Errorf("partial ambiguous batch %d", first)
			}
			if count == 8 {
				ambiguousPresent++
			} else {
				ambiguousAbsent++
			}
		}
	}
	emit(map[string]any{"event": "witness_verified", "rows": len(present), "ack_batches": ack, "ambiguous_present_batches": ambiguousPresent, "ambiguous_absent_batches": ambiguousAbsent, "wrong": 0})
	return nil
}

// Keep a bounded independent historical reader live past the replica session timeout.
func liveRead(ctx context.Context, pg, backend *sql.DB, n, customers int) error {
	tail := fmt.Sprintf("SELECT %s FROM live_orders WHERE order_id>%d AND customer_id>%d ORDER BY order_id LIMIT 64", columns, n*9/10, customers/2)
	count, hash, err := digest(ctx, pg, "WITH live_orders AS ("+source+") "+tail)
	if err != nil {
		return err
	}
	end := time.Now().Add(60 * time.Second)
	for time.Now().Before(end) {
		started := time.Now()
		got, actual, e := digest(ctx, backend, "WITH live_orders AS (SELECT "+columns+" FROM pipeline.current_comparison) "+tail)
		if e != nil {
			return e
		}
		if got != count || actual != hash {
			return fmt.Errorf("wrong successful live historical read")
		}
		emit(map[string]any{"event": "query", "query": "historical-64", "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6, "rows": got})
		time.Sleep(200 * time.Millisecond)
	}
	return nil
}
