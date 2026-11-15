// Application workload: native PostgreSQL COPY, bounded queries and recovery writes.
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
)

var ctx = context.Background()
var root = "/results"

func check(err error) {
	if err != nil {
		panic(err)
	}
}

func save(name string, value any) {
	data, err := json.MarshalIndent(value, "", "  ")
	check(err)
	path := filepath.Join(root, name+".json")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	check(err)
	_, err = file.Write(append(data, '\n'))
	check(err)
	check(file.Sync())
	check(file.Close())
	fmt.Println("Saved", path)
}

func record(file *os.File, value any) {
	check(json.NewEncoder(file).Encode(value))
	check(file.Sync())
}

func connect(dsn string) (*pgx.Conn, error) {
	config, err := pgx.ParseConfig(dsn)
	if err != nil {
		return nil, err
	}
	config.ConnectTimeout = 5 * time.Second
	config.RuntimeParams["statement_timeout"] = "10000"
	return pgx.ConnectConfig(ctx, config)
}

func count() int {
	text := os.Getenv("ROWS")
	if text == "" {
		text = "1000000"
	}
	n, err := strconv.Atoi(text)
	check(err)
	if n < 1 {
		panic("ROWS must be positive")
	}
	return n
}

func systems() []struct{ name, dsn string } {
	return []struct{ name, dsn string }{{"neon", os.Getenv("NEON_DSN")}, {"postgres", os.Getenv("PG_DSN")}}
}

func seed() {
	if _, err := os.Stat(filepath.Join(root, "seed.json")); !os.IsNotExist(err) {
		panic("seed exists or cannot be inspected; archive results for a fresh lifecycle")
	}
	n := count()
	journal, err := os.OpenFile(filepath.Join(root, "commits.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	check(err)
	defer journal.Close()
	out := map[string]any{}
	for _, system := range systems() {
		conn, err := connect(system.dsn)
		check(err)
		_, err = conn.Exec(ctx, "CREATE TABLE orders (order_id BIGINT PRIMARY KEY,customer_id INTEGER NOT NULL,amount INTEGER NOT NULL,payload BYTEA NOT NULL)")
		check(err)
		_, err = conn.Exec(ctx, "CREATE INDEX ON orders(customer_id,order_id)")
		check(err)
		var lat []float64
		start := time.Now()
		for first := 0; first < n; first += 10000 {
			last := min(first+10000, n)
			record(journal, map[string]any{"system": system.name, "stage": "attempt", "first": first, "last": last, "at": float64(time.Now().UnixNano()) / 1e9})
			begin := time.Now()
			copied, err := conn.CopyFrom(ctx, pgx.Identifier{"orders"}, []string{"order_id", "customer_id", "amount", "payload"}, pgx.CopyFromSlice(last-first, func(index int) ([]any, error) {
				i := int64(first + index)
				body := sha256.Sum256([]byte(fmt.Sprintf("order:%d", i)))
				return []any{i, int32(i % 10000), int32((i * 17) % 100000), body[:]}, nil
			}))
			check(err)
			if copied != int64(last-first) {
				panic("short COPY")
			}
			lat = append(lat, float64(time.Since(begin))/float64(time.Millisecond))
			record(journal, map[string]any{"system": system.name, "stage": "ack", "first": first, "last": last, "at": float64(time.Now().UnixNano()) / 1e9})
		}
		elapsed := time.Since(start).Seconds()
		sort.Float64s(lat)
		out[system.name] = map[string]any{"rows": n, "seconds": elapsed, "rows_per_second": float64(n) / elapsed, "batch_rows": 10000, "batch_p50_ms": median(lat), "batch_p99_ms": lat[min(len(lat)-1, int(float64(len(lat))*.99))], "acknowledged_rows": n, "errors": 0}
		check(conn.Close(ctx))
	}
	save("seed", out)
}

func median(values []float64) float64 {
	n := len(values)
	if n%2 == 0 {
		return (values[n/2-1] + values[n/2]) / 2
	}
	return values[n/2]
}

func benchmark() {
	n := count()
	var total, customerTotal, customerCount int64
	for i := int64(0); i < int64(n); i++ {
		amount := (i * 17) % 100000
		total += amount
		if i%10000 == 42 {
			customerTotal += amount
			customerCount++
		}
	}
	out := map[string]any{}
	for _, system := range systems() {
		conn, err := connect(system.dsn)
		check(err)
		queries := []struct {
			name, sql string
			args      []any
			n, amount int64
		}{
			{"customer-orders", "SELECT count(*),COALESCE(sum(amount),0) FROM orders WHERE customer_id=$1", []any{int32(42)}, customerCount, customerTotal},
			{"all-orders", "SELECT count(*),COALESCE(sum(amount),0) FROM orders", nil, int64(n), total},
		}
		for _, query := range queries {
			var lat []float64
			for run := 0; run < 21; run++ {
				begin := time.Now()
				var actualN, amount int64
				check(conn.QueryRow(ctx, query.sql, query.args...).Scan(&actualN, &amount))
				elapsed := float64(time.Since(begin)) / float64(time.Millisecond)
				if actualN != query.n || amount != query.amount {
					panic("incorrect query result: " + system.name + "/" + query.name)
				}
				if run > 0 {
					lat = append(lat, elapsed)
				}
			}
			sort.Float64s(lat)
			out[system.name+"/"+query.name] = map[string]any{"samples": 20, "warmup": 1, "p50_ms": median(lat), "p99_ms": lat[len(lat)-1], "errors": 0}
		}
		check(conn.Close(ctx))
	}
	save("query-benchmark", out)
}

func churn() {
	caseName := os.Getenv("FAULT_CASE")
	if caseName == "" || filepath.Base(caseName) != caseName {
		panic("invalid FAULT_CASE")
	}
	dsn := os.Getenv("NEON_DSN")
	conn, err := connect(dsn)
	check(err)
	_, err = conn.Exec(ctx, "CREATE TABLE IF NOT EXISTS recovery_events (event_id BIGINT PRIMARY KEY,customer_id INTEGER NOT NULL,amount BIGINT NOT NULL,payload BYTEA NOT NULL)")
	check(err)
	defer func() {
		if conn != nil {
			_ = conn.Close(ctx)
		}
	}()
	journal, err := os.OpenFile(filepath.Join(root, "fault-"+caseName+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer journal.Close()
	firstKey := time.Now().UnixMilli() * 1000
	for batch := 0; batch < 18000; batch++ {
		if _, err := os.Stat(filepath.Join(root, "stop-"+caseName)); err == nil {
			return
		} else if !os.IsNotExist(err) {
			check(err)
		}
		first := firstKey + int64(batch)*100
		record(journal, map[string]any{"stage": "attempt", "first": first, "count": 100, "at": float64(time.Now().UnixNano()) / 1e9})
		begin := time.Now()
		if conn == nil || conn.IsClosed() {
			conn, err = connect(dsn)
		}
		var flushLSN string
		if err == nil {
			var copied int64
			copied, err = conn.CopyFrom(ctx, pgx.Identifier{"recovery_events"}, []string{"event_id", "customer_id", "amount", "payload"}, pgx.CopyFromSlice(100, func(index int) ([]any, error) {
				key := first + int64(index)
				body := sha256.Sum256([]byte(fmt.Sprintf("recovery:%d", key)))
				return []any{key, int32(key % 10000), key * 3, body[:]}, nil
			}))
			if err == nil && copied != 100 {
				err = fmt.Errorf("short COPY: %d", copied)
			}
			if err == nil {
				err = conn.QueryRow(ctx, "SELECT pg_current_wal_flush_lsn()::text").Scan(&flushLSN)
			}
		}
		entry := map[string]any{"stage": "ack", "first": first, "count": 100, "at": float64(time.Now().UnixNano()) / 1e9, "flush_lsn": flushLSN, "latency_ms": float64(time.Since(begin)) / float64(time.Millisecond)}
		if err != nil {
			entry["stage"], entry["error"] = "ambiguous", err.Error()
			if conn != nil {
				_ = conn.Close(ctx)
			}
			conn, err = nil, nil
		}
		record(journal, entry)
		time.Sleep(100 * time.Millisecond)
	}
	panic("producer expired before recovery")
}

// Independently sample the fully verified baseline while recovery writes run.
// This checks availability/body correctness, not visibility of newly written rows.
func watch() {
	name := os.Getenv("FAULT_CASE")
	if name == "" || filepath.Base(name) != name {
		panic("invalid FAULT_CASE")
	}
	file, err := os.OpenFile(filepath.Join(root, "reads-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	var conn *pgx.Conn
	defer func() {
		if conn != nil {
			_ = conn.Close(ctx)
		}
	}()
	n := int64(count())
	for sequence := int64(0); sequence < 18000; sequence++ {
		if _, err := os.Stat(filepath.Join(root, "stop-"+name)); err == nil {
			return
		} else if !os.IsNotExist(err) {
			check(err)
		}
		key := (sequence * 7919) % n
		started := time.Now().UTC()
		outcome, message := "ok", ""
		readCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		if conn == nil || conn.IsClosed() {
			conn, err = connect(os.Getenv("NEON_DSN"))
		}
		var customer, amount int64
		var payload []byte
		if err == nil {
			err = conn.QueryRow(readCtx, "SELECT customer_id,amount,payload FROM orders WHERE order_id=$1", key).Scan(&customer, &amount, &payload)
		}
		cancel()
		if errors.Is(err, pgx.ErrNoRows) {
			outcome = "missing"
			err = nil
		} else if err != nil {
			outcome, message = "error", err.Error()
			if conn != nil {
				_ = conn.Close(ctx)
			}
			conn = nil
			err = nil
		} else {
			body := sha256.Sum256([]byte(fmt.Sprintf("order:%d", key)))
			if customer != key%10000 || amount != (key*17)%100000 || !bytes.Equal(payload, body[:]) {
				outcome = "wrong"
			}
		}
		record(file, map[string]any{"started_at": started, "at": time.Now().UTC(), "outcome": outcome, "order_id": key, "latency_ms": float64(time.Since(started)) / float64(time.Millisecond), "error": message})
		time.Sleep(100 * time.Millisecond)
	}
	panic("reader expired before recovery")
}

func main() {
	check(os.MkdirAll(root, 0755))
	if len(os.Args) != 2 {
		panic("usage: workload seed|benchmark|churn")
	}
	switch os.Args[1] {
	case "seed":
		seed()
	case "benchmark":
		benchmark()
	case "churn":
		churn()
	case "watch":
		watch()
	default:
		panic("unknown workload")
	}
}
