package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// Read immutable, already verified baseline orders while the writer appends
// recovery events. This measures availability/correctness, not fresh-write lag.
func watch() {
	queryTimeout = 10 * time.Second
	system := env("FAULT_SYSTEM", "starrocks")
	if system != "starrocks" && system != "clickhouse" {
		panic("invalid FAULT_SYSTEM")
	}
	name := os.Getenv("FAULT_CASE")
	if name == "" || filepath.Base(name) != name {
		panic("invalid FAULT_CASE")
	}
	file, err := os.OpenFile(filepath.Join(results, "reads-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	httpClient.Timeout = 10 * time.Second
	n := uint64(count())
	coordinator := ""
	for sequence := uint64(0); sequence < 18000; sequence++ {
		if _, err := os.Stat(filepath.Join(results, "stop-"+name)); err == nil {
			return
		} else if !os.IsNotExist(err) {
			check(err)
		}
		key := (sequence * 7919) % n
		started := time.Now().UTC()
		outcome, message := "ok", ""
		if system == "clickhouse" && coordinator == "" {
			coordinator, err = healthyHTTP(env("CH_WRITERS", "ch00,ch10,ch20"), "8123", "/ping", "default", "local-only-clickhouse")
			if err == nil {
				check(os.Setenv("CH_HOST", coordinator))
			}
		}
		var data [][]string
		if err == nil {
			from := "orders_lab.orders"
			if system == "clickhouse" {
				from += " FINAL"
			}
			data, err = query(system, fmt.Sprintf("SELECT customer_id,order_id,amount,status,version,fingerprint FROM %s WHERE customer_id=%d AND order_id=%d LIMIT 2", from, key%10000, key))
		}
		if err != nil {
			outcome, message = "error", err.Error()
			preferredFrontend = nil
			coordinator = ""
			err = nil
		} else if len(data) == 0 {
			outcome = "missing"
		} else if len(data) != 1 {
			outcome = "wrong"
		} else {
			row := parseOrder(data[0])
			if row != expected(key, true) {
				outcome = "wrong"
				if expected(key, true).Version == 2 && row == expected(key, false) {
					outcome = "stale"
				}
			}
		}
		journal(file, map[string]any{"system": system, "started_at": started, "at": time.Now().UTC(), "outcome": outcome, "order_id": key, "latency_ms": float64(time.Since(started)) / float64(time.Millisecond), "error": message})
		time.Sleep(100 * time.Millisecond)
	}
	panic("reader expired before recovery")
}
