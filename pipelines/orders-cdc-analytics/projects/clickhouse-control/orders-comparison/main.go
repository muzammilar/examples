// Identical MySQL-wire reader/oracle for both analytical backends.
package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"syscall"
	"time"

	_ "github.com/go-sql-driver/mysql"
	_ "github.com/jackc/pgx/v5/stdlib"
)

const columns = "order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,version"
const source = "SELECT o.id AS order_id,o.customer_id,o.amount_cents,c.region,o.revision AS order_revision,c.revision AS customer_revision,o.event_second,o.revision*1000000+c.revision AS version FROM orders o JOIN customers c ON o.customer_id=c.id WHERE NOT o.cancelled"

func emit(row map[string]any) {
	row["utc_ns"] = time.Now().UnixNano()
	if err := json.NewEncoder(os.Stdout).Encode(row); err != nil {
		panic(err)
	}
}
func open(driver, dsn string) (*sql.DB, error) {
	db, err := sql.Open(driver, dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	db.SetConnMaxLifetime(5 * time.Minute)
	return db, nil
}
func scan(rows *sql.Rows) ([]string, error) {
	names, e := rows.Columns()
	if e != nil {
		return nil, e
	}
	values := make([]string, len(names))
	ptr := make([]any, len(names))
	for i := range values {
		ptr[i] = &values[i]
	}
	if e = rows.Scan(ptr...); e != nil {
		return nil, e
	}
	return values, nil
}
func digest(ctx context.Context, db *sql.DB, query string) (int64, string, error) {
	rows, err := db.QueryContext(ctx, query)
	if err != nil {
		return 0, "", err
	}
	defer rows.Close()
	hash := sha256.New()
	encoder := json.NewEncoder(hash)
	var count int64
	for rows.Next() {
		row, e := scan(rows)
		if e != nil {
			return count, "", e
		}
		if e = encoder.Encode(row); e != nil {
			return count, "", e
		}
		count++
	}
	if err = rows.Err(); err != nil {
		return count, "", err
	}
	return count, hex.EncodeToString(hash.Sum(nil)), nil
}
func verify(ctx context.Context, pg, backend *sql.DB) error {
	// Source is fenced by the controller; recheck its complete fingerprint each phase.
	expected, e := pg.QueryContext(ctx, source+" ORDER BY o.id LIMIT 2000001")
	if e != nil {
		return e
	}
	defer expected.Close()
	actual, e := backend.QueryContext(ctx, "SELECT "+columns+" FROM pipeline.current_comparison ORDER BY order_id LIMIT 2000001")
	if e != nil {
		return e
	}
	defer actual.Close()
	var count int64
	hash := sha256.New()
	encode := json.NewEncoder(hash)
	started := time.Now()
	for expected.Next() {
		want, e := scan(expected)
		if e != nil {
			return e
		}
		if !actual.Next() {
			return fmt.Errorf("missing body: %v", actual.Err())
		}
		got, e := scan(actual)
		if e != nil {
			return e
		}
		if len(got) != len(want) {
			return fmt.Errorf("field count differs")
		}
		for i := range want {
			if got[i] != want[i] {
				return fmt.Errorf("body mismatch id=%s field=%d expected=%s observed=%s", want[0], i, want[i], got[i])
			}
		}
		if e = encode.Encode(want); e != nil {
			return e
		}
		count++
	}
	if e = expected.Err(); e != nil {
		return e
	}
	if actual.Next() {
		return fmt.Errorf("extra sink body")
	}
	if e = actual.Err(); e != nil {
		return e
	}
	emit(map[string]any{"event": "verified", "rows": count, "wrong": 0, "source_sha256": hex.EncodeToString(hash.Sum(nil)), "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6})
	return nil
}
func benchmark(ctx context.Context, pg, backend *sql.DB, n int) error {
	customers, e := strconv.Atoi(os.Getenv("CUSTOMER_COUNT"))
	if e != nil || customers < 100 || customers > 10000 {
		return fmt.Errorf("invalid customer bound")
	}
	queries := []struct{ name, tail string }{
		{"point", fmt.Sprintf("SELECT %s FROM live_orders WHERE order_id=%d ORDER BY order_id", columns, n-7)},
		{"historical-64", fmt.Sprintf("SELECT %s FROM live_orders WHERE order_id>%d AND customer_id>%d ORDER BY order_id LIMIT 64", columns, n*9/10, customers/2)},
		{"regions", "SELECT region,count(*),sum(amount_cents) FROM live_orders GROUP BY region ORDER BY region LIMIT 32"},
	}
	for _, query := range queries {
		sourceSQL := "WITH live_orders AS (" + source + ") " + query.tail
		wantCount, wantHash, e := digest(ctx, pg, sourceSQL)
		if e != nil {
			return e
		}
		for trial := 0; trial < 3; trial++ {
			// Reopen for a client-cold connection, never claim physical disk-cold.
			backend.SetMaxIdleConns(0)
			backend.SetMaxIdleConns(1)
			connectionStart := time.Now()
			connection, e := backend.Conn(ctx)
			if e != nil {
				return e
			}
			if e = connection.PingContext(ctx); e != nil {
				connection.Close()
				return e
			}
			connectMS := float64(time.Since(connectionStart).Nanoseconds()) / 1e6
			connection.Close()
			backend.SetMaxIdleConns(1)
			for iteration := 0; iteration < 41; iteration++ {
				started := time.Now()
				count, hash, e := digest(ctx, backend, "WITH live_orders AS (SELECT "+columns+" FROM pipeline.current_comparison) "+query.tail)
				duration := float64(time.Since(started).Nanoseconds()) / 1e6
				if e != nil {
					return e
				}
				if count != wantCount || hash != wantHash {
					return fmt.Errorf("wrong successful query %s count %d expected %d", query.name, count, wantCount)
				}
				cache := "warm"
				if iteration == 0 {
					cache = "new connection; server/OS caches not cleared"
				}
				emit(map[string]any{"event": "query", "query": query.name, "trial": trial, "iteration": iteration, "warmup": iteration >= 1 && iteration <= 10, "cache": cache, "connect_ms": connectMS, "duration_ms": duration, "rows": count, "expected_sha256": wantHash})
			}
		}
	}
	return nil
}
func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("mode required")
	}
	mode := os.Args[1]
	if mode == "idle" {
		for {
			time.Sleep(time.Hour)
		}
	}
	pg, err := open("pgx", "postgres://app:local-app-only@postgres:5432/orders?sslmode=disable&connect_timeout=5")
	if err != nil {
		return err
	}
	defer pg.Close()
	backendName := os.Getenv("BACKEND")
	host := os.Getenv("BACKEND_HOST")
	dsn := ""
	switch backendName {
	case "doris":
		if host != "fe1" {
			return fmt.Errorf("unexpected Doris host")
		}
		dsn = "root@tcp(fe1:9030)/pipeline?timeout=5s&readTimeout=60s&writeTimeout=30s&enable_sql_cache=false&enable_query_cache=false"
		if replica := os.Getenv("REPLICA_INDEX"); replica != "" {
			index, e := strconv.Atoi(replica)
			if e != nil || index < 0 || index > 2 {
				return fmt.Errorf("invalid fixed replica")
			}
			dsn += "&skip_missing_version=false&fallback_other_replica_when_fixed_corrupt=false&use_fix_replica=" + replica
		}
	case "clickhouse":
		if host != "ch1" && host != "ch2" && host != "ch3" {
			return fmt.Errorf("unexpected ClickHouse host")
		}
		dsn = "lab:local-control-only@tcp(" + host + ":9004)/pipeline?timeout=5s&readTimeout=60s&writeTimeout=30s"
	default:
		return fmt.Errorf("unknown backend")
	}
	backend, err := open("mysql", dsn)
	if err != nil {
		return err
	}
	defer backend.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	emit(map[string]any{"event": "configuration", "backend": backendName, "host": host, "pool": 1, "in_flight": 1, "retry": 0, "query_read_timeout_s": 60, "protocol": "same go-sql-driver/mysql on both backends"})
	switch mode {
	case "verify":
		return verify(ctx, pg, backend)
	case "write-witness", "verify-witness":
		run := os.Getenv("CASE_ID")
		if !casePattern.MatchString(run) || backendName != "clickhouse" {
			return fmt.Errorf("owned witness identity/backend required")
		}
		if mode == "write-witness" {
			return writeWitness(ctx, backend, run)
		}
		return verifyWitness(ctx, backend, run)
	case "live-read":
		n, e := strconv.Atoi(os.Getenv("ORDER_COUNT"))
		if e != nil || n < 10000 || n > 2000000 {
			return fmt.Errorf("invalid order bound")
		}
		customers, e := strconv.Atoi(os.Getenv("CUSTOMER_COUNT"))
		if e != nil || customers < 100 || customers > 10000 {
			return fmt.Errorf("invalid customer bound")
		}
		return liveRead(ctx, pg, backend, n, customers)
	case "benchmark":
		n, e := strconv.Atoi(os.Getenv("ORDER_COUNT"))
		if e != nil || n < 10000 || n > 2000000 {
			return fmt.Errorf("invalid order bound")
		}
		return benchmark(ctx, pg, backend, n)
	default:
		return fmt.Errorf("unknown mode")
	}
}
func main() {
	if source, e := os.ReadFile("/source.sha256"); e == nil {
		emit(map[string]any{"event": "source", "manifest": string(source)})
	}
	err := run()
	var r syscall.Rusage
	if e := syscall.Getrusage(syscall.RUSAGE_SELF, &r); e == nil {
		emit(map[string]any{"event": "resources", "peak_rss_kib": r.Maxrss, "cpu_user_s": float64(r.Utime.Sec) + float64(r.Utime.Usec)/1e6, "cpu_system_s": float64(r.Stime.Sec) + float64(r.Stime.Usec)/1e6})
	}
	if err != nil {
		emit(map[string]any{"event": "fatal", "error": err.Error()})
		os.Exit(1)
	}
}
