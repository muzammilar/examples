// Independent full-row oracle and native Doris load receipts for immutable batches.
package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"strconv"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

func emit(v any) {
	if err := json.NewEncoder(os.Stdout).Encode(v); err != nil {
		panic(err)
	}
}
func number(name string, def int64) int64 {
	s := os.Getenv(name)
	if s == "" {
		return def
	}
	n, e := strconv.ParseInt(s, 10, 64)
	if e != nil || n < 1 {
		panic("invalid " + name)
	}
	return n
}
func native(ctx context.Context, db *sql.DB, query string) ([]map[string]string, error) {
	rows, e := db.QueryContext(ctx, query)
	if e != nil {
		return nil, e
	}
	defer rows.Close()
	cols, e := rows.Columns()
	if e != nil {
		return nil, e
	}
	result := []map[string]string{}
	for rows.Next() {
		values := make([]sql.NullString, len(cols))
		args := make([]any, len(cols))
		for i := range values {
			args[i] = &values[i]
		}
		if e = rows.Scan(args...); e != nil {
			return nil, e
		}
		record := map[string]string{}
		for i, col := range cols {
			if values[i].Valid {
				record[col] = values[i].String
			}
		}
		result = append(result, record)
	}
	return result, rows.Err()
}
func verify(ctx context.Context, db *sql.DB, batch string, count, customers int64) error {
	rows, e := db.QueryContext(ctx, "SELECT order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,customer_shard FROM pipeline.orders WHERE batch=? ORDER BY order_id", batch)
	if e != nil {
		return e
	}
	defer rows.Close()
	var n int64
	totals := map[string][2]int64{}
	for rows.Next() {
		var id, customer, amount, ov, cv, second, shard int64
		var region string
		if e = rows.Scan(&id, &customer, &amount, &region, &ov, &cv, &second, &shard); e != nil {
			return e
		}
		n++
		expectedCustomer := 1 + (n-1)%customers
		if id != n || customer != expectedCustomer || amount != 100+n%1000 || region != fmt.Sprintf("region-%d", customer%16) || ov != 1 || cv != 1 || second != 1700000000+n%86400 || shard != customer%3 {
			return fmt.Errorf("wrong body at order %d", n)
		}
		sum := totals[region]
		sum[0]++
		sum[1] += amount
		totals[region] = sum
	}
	if e = rows.Err(); e != nil {
		return e
	}
	if n != count {
		return fmt.Errorf("rows %d != %d", n, count)
	}
	aggregates, e := db.QueryContext(ctx, "SELECT region,orders,amount_cents FROM pipeline.regions WHERE batch=?", batch)
	if e != nil {
		return e
	}
	defer aggregates.Close()
	seen := map[string]bool{}
	for aggregates.Next() {
		var region string
		var n, amount int64
		if e = aggregates.Scan(&region, &n, &amount); e != nil {
			return e
		}
		if seen[region] || totals[region] != [2]int64{n, amount} {
			return fmt.Errorf("wrong aggregate %s", region)
		}
		seen[region] = true
	}
	if e = aggregates.Err(); e != nil {
		return e
	}
	if len(seen) != len(totals) {
		return fmt.Errorf("missing region aggregates")
	}
	return nil
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
func run() error {
	defer func() {
		counters := map[string]string{}
		for _, name := range []string{"cpu.stat", "memory.current", "memory.peak", "memory.events", "io.stat"} {
			body, e := os.ReadFile(filepath.Join("/sys/fs/cgroup", name))
			if e != nil {
				counters[name] = "unavailable: " + e.Error()
			} else {
				counters[name] = string(body)
			}
		}
		emit(map[string]any{"actor_counters": counters})
	}()
	if len(os.Args) == 2 && os.Args[1] == "cgroup" {
		return nil
	}

	if len(os.Args) < 2 {
		return fmt.Errorf("expected mode")
	}
	mode := os.Args[1]
	batch := "baseline"
	if len(os.Args) > 2 {
		batch = os.Args[2]
	}
	if batch != "baseline" && batch != "executor" && batch != "datanode" {
		return fmt.Errorf("invalid batch")
	}
	count, customers := number("ORDER_COUNT", 1000000), number("CUSTOMER_COUNT", 10000)
	build, _ := debug.ReadBuildInfo()
	emit(map[string]any{"client_config": map[string]any{"driver": "go-sql-driver/mysql 1.11.0", "go": build.GoVersion, "pool": 1, "max_inflight": 1, "connect_timeout_s": 5, "read_timeout_s": 180, "write_timeout_s": 10, "context_timeout_s": 600, "supervisor_timeout_s": 900, "mutation_retry": "no blind retries; reconcile native label", "batch": batch}})

	replica := os.Getenv("REPLICA_INDEX")
	extra := ""
	if replica != "" {
		if replica != "0" && replica != "1" && replica != "2" {
			return fmt.Errorf("invalid replica")
		}
		extra = "&skip_missing_version=false&fallback_other_replica_when_fixed_corrupt=false&use_fix_replica=" + replica
	}
	db, e := sql.Open("mysql", "root@tcp(fe1:9030)/?timeout=5s&readTimeout=180s&writeTimeout=10s"+extra)
	if e != nil {
		return e
	}
	defer db.Close()
	db.SetMaxOpenConns(1)
	ctx, cancel := context.WithTimeout(context.Background(), 600*time.Second)
	defer cancel()
	started := time.Now()
	switch mode {
	case "setup":
		statements := []string{"CREATE DATABASE IF NOT EXISTS pipeline",
			"CREATE TABLE IF NOT EXISTS pipeline.orders (batch VARCHAR(32),order_id BIGINT,customer_id BIGINT,amount_cents BIGINT,region VARCHAR(64),order_revision BIGINT,customer_revision BIGINT,event_second BIGINT,customer_shard BIGINT) UNIQUE KEY(batch,order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 3 PROPERTIES ('replication_num'='3','min_load_replica_num'='2','enable_unique_key_merge_on_write'='true','compression'='ZSTD')",
			"CREATE TABLE IF NOT EXISTS pipeline.regions (batch VARCHAR(32),region VARCHAR(64),orders BIGINT,amount_cents BIGINT) UNIQUE KEY(batch,region) DISTRIBUTED BY HASH(region) BUCKETS 3 PROPERTIES ('replication_num'='3','min_load_replica_num'='2','enable_unique_key_merge_on_write'='true','compression'='ZSTD')"}
		for _, q := range statements {
			if _, e = db.ExecContext(ctx, q); e != nil {
				return e
			}
		}
		for _, table := range []string{"orders", "regions"} {
			if _, e = db.ExecContext(ctx, "ALTER TABLE pipeline."+table+" SET ('min_load_replica_num'='2')"); e != nil {
				return e
			}
		}

	case "topology":
		for _, q := range []string{"SELECT @@version_comment", "SHOW FRONTENDS", "SHOW BACKENDS", "SHOW TABLETS FROM pipeline.orders", "SHOW TABLETS FROM pipeline.regions", "SHOW CREATE TABLE pipeline.orders", "SHOW CREATE TABLE pipeline.regions", "SHOW LOAD FROM pipeline"} {
			v, e := native(ctx, db, q)
			if e != nil {
				return e
			}
			emit(map[string]any{"query": q, "rows": v})
		}
	case "load":
		// Strict insert, stable label, no automatic write retry. Existing receipt must be FINISHED.
		if _, e = db.ExecContext(ctx, "SET enable_insert_strict=true"); e != nil {
			return e
		}
		for _, table := range []string{"orders", "regions"} {
			label := "hdfs_" + batch + "_" + table
			receipts, e := native(ctx, db, "SHOW LOAD FROM pipeline WHERE LABEL = '"+label+"'")
			if e != nil {
				return e
			}
			if len(receipts) > 0 {
				if len(receipts) != 1 || receipts[0]["State"] != "FINISHED" {
					return fmt.Errorf("ambiguous/rejected prior receipt: %v", receipts)
				}
				emit(map[string]any{"mode": "retained-label", "label": label, "receipt": receipts})
				continue
			}
			columns := "order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,cast(customer_shard AS BIGINT)"
			glob := "/customer_shard=*/*.parquet"
			partition := ", 'path_partition_keys'='customer_shard'"
			if table == "regions" {
				columns = "region,orders,amount_cents"
				glob = "/*.parquet"
				partition = ""
			}
			query := fmt.Sprintf("INSERT INTO pipeline.%s WITH LABEL %s SELECT '%s',%s FROM HDFS('uri'='hdfs://namenode:9000/lab/published/%s/%s%s','fs.defaultFS'='hdfs://namenode:9000','hadoop.username'='hadoop','format'='parquet','dfs.client.use.datanode.hostname'='true'%s)", table, label, batch, columns, batch, table, glob, partition)
			emit(map[string]any{"mode": "intent", "label": label, "query": query})
			loadStart := time.Now()
			if _, e = db.ExecContext(ctx, query); e != nil {
				return fmt.Errorf("reconcile label %s before retry: %w", label, e)
			}
			emit(map[string]any{"mode": "ack", "label": label, "wall_ms": float64(time.Since(loadStart).Nanoseconds()) / 1e6})
		}
	case "query":
		if e = queryTrials(ctx, db, batch, count, customers); e != nil {
			return e
		}
	case "verify":
		if e = verify(ctx, db, batch, count, customers); e != nil {
			return e
		}
	default:
		return fmt.Errorf("unknown mode")
	}
	emit(map[string]any{"passed": true, "mode": mode, "batch": batch, "rows": count, "customers": customers, "replica": replica, "wall_ms": float64(time.Since(started).Nanoseconds()) / 1e6})
	return nil
}
