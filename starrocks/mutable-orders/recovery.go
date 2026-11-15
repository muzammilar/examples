package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type faultRecord struct {
	System string `json:"system"`
	Stage  string `json:"stage"`
	First  uint64 `json:"first"`
	Count  uint64 `json:"count"`
}

func recoveryOrder(key uint64) order {
	return order{uint32(key % 10000), key, uint32(key % 100000), "recovery", 1, fingerprint(key, "recovery")}
}

func faultSetting(name string, fallback, maximum int) int {
	n, err := strconv.Atoi(env(name, strconv.Itoa(fallback)))
	check(err)
	if n < 1 || n > maximum {
		panic("invalid " + name)
	}
	return n
}

func churn() {
	httpClient.Timeout = 10 * time.Second
	system := env("FAULT_SYSTEM", "starrocks")
	if system != "starrocks" && system != "clickhouse" {
		panic("invalid FAULT_SYSTEM")
	}
	name := os.Getenv("FAULT_CASE")
	if name == "" || filepath.Base(name) != name {
		panic("invalid FAULT_CASE")
	}
	file, err := os.OpenFile(filepath.Join(results, "fault-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	batchRows := faultSetting("FAULT_BATCH_ROWS", 1000, 10000)
	pause := time.Duration(faultSetting("FAULT_PAUSE_MS", 1000, 10000)) * time.Millisecond
	firstKey := uint64(time.Now().UnixMilli()) * 1000
	for batch := 0; batch < 18000; batch++ {
		if _, err := os.Stat(filepath.Join(results, "stop-"+name)); err == nil {
			return
		} else if !os.IsNotExist(err) {
			check(err)
		}
		first := firstKey + uint64(batch)*uint64(batchRows)
		data := make([]order, batchRows)
		for i := range data {
			data[i] = recoveryOrder(first + uint64(i))
		}
		label := fmt.Sprintf("%s-%d", name, batch)
		journal(file, map[string]any{"system": system, "stage": "attempt", "first": first, "count": len(data), "at": time.Now().UTC(), "label": label})
		begin := time.Now()
		err := insertRows(system, "recovery", label, data)
		stage, message := "ack", ""
		if err != nil {
			stage, message = "ambiguous", err.Error()
		}
		journal(file, map[string]any{"system": system, "stage": stage, "first": first, "count": len(data), "at": time.Now().UTC(), "error": message, "latency_ms": float64(time.Since(begin)) / float64(time.Millisecond)})
		time.Sleep(pause)
	}
	panic("continuous producer expired before recovery")
}

func verifyRecovery() {
	files, err := filepath.Glob(filepath.Join(results, "fault-*.jsonl"))
	check(err)
	out := map[string]any{}
	for _, system := range systemNames() {
		attempted, acked, seen := map[uint64]bool{}, map[uint64]bool{}, map[uint64]bool{}
		cases := 0
		ambiguous := 0
		var batches []faultRecord
		for _, path := range files {
			file, err := os.Open(path)
			check(err)
			scanner := bufio.NewScanner(file)
			matched := false
			for scanner.Scan() {
				var r faultRecord
				check(json.Unmarshal(scanner.Bytes(), &r))
				if r.System != system {
					continue
				}
				matched = true
				if r.Stage == "attempt" {
					batches = append(batches, r)
				}
				if r.Stage == "ambiguous" {
					ambiguous++
				}
				for i := uint64(0); i < r.Count; i++ {
					key := r.First + i
					if r.Stage == "attempt" {
						attempted[key] = true
					}
					if r.Stage == "ack" {
						acked[key] = true
					}
				}
			}
			check(scanner.Err())
			check(file.Close())
			if matched {
				cases++
			}
		}
		from := "orders_lab.recovery"
		if system == "clickhouse" {
			from += " FINAL"
		}
		check(readOrders(system, from, len(attempted)+1, func(values []string) error {
			row := parseOrder(values)
			if !attempted[row.ID] || seen[row.ID] || row != recoveryOrder(row.ID) {
				return fmt.Errorf("unexpected, duplicate or corrupt %s recovery row %d", system, row.ID)
			}
			seen[row.ID] = true
			return nil
		}))
		for key := range acked {
			if !seen[key] {
				panic(fmt.Sprintf("lost %s acknowledged row %d", system, key))
			}
		}
		partial := 0
		for _, batch := range batches {
			observed := uint64(0)
			for i := uint64(0); i < batch.Count; i++ {
				if seen[batch.First+i] {
					observed++
				}
			}
			if observed != 0 && observed != batch.Count {
				partial++
			}
		}
		if system == "starrocks" && partial > 0 {
			panic("partially committed StarRocks Stream Load transaction")
		}
		if system == "starrocks" && len(seen) > 0 {
			srReplicaChecksums("recovery")
		}
		if system == "clickhouse" && len(seen) > 0 {
			for shard := 0; shard < chShards(); shard++ {
				for replica := 0; replica < chCopies(); replica++ {
					host := fmt.Sprintf("ch%d%d", shard, replica)
					old := os.Getenv("CH_HOST")
					check(os.Setenv("CH_HOST", host))
					local := map[uint64]bool{}
					check(readOrders(system, "orders_lab.recovery_local FINAL", len(attempted)+1, func(values []string) error {
						row := parseOrder(values)
						if int(row.Customer)%chShards() != shard || !seen[row.ID] || local[row.ID] || row != recoveryOrder(row.ID) {
							return fmt.Errorf("wrong %s recovery placement/body %d", host, row.ID)
						}
						local[row.ID] = true
						return nil
					}))
					for key := range seen {
						if int(key%10000)%chShards() == shard && !local[key] {
							panic(fmt.Sprintf("missing recovery row on %s", host))
						}
					}
					check(os.Setenv("CH_HOST", old))
				}
			}
		}
		out[system] = map[string]any{"cases": cases, "acknowledged_rows": len(acked), "observed_attempted_rows": len(seen), "ambiguous_batches": ambiguous, "missing_acknowledged": 0, "wrong_or_duplicate": 0, "partial_unacknowledged_batches": partial, "batch_atomicity": "StarRocks enforces atomic Stream Load; ClickHouse Distributed writes may partially commit across shards"}
	}
	save("recovery-history", out)
}
