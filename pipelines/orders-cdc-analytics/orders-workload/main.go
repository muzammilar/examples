package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"syscall"
	"time"

	_ "github.com/go-sql-driver/mysql"
	_ "github.com/jackc/pgx/v5/stdlib"
)

func emit(row map[string]any) {
	row["utc_ns"] = time.Now().UnixNano()
	if err := json.NewEncoder(os.Stdout).Encode(row); err != nil {
		panic(err)
	}
}
func count(name string, fallback int) (int, error) {
	value := os.Getenv(name)
	if value == "" {
		return fallback, nil
	}
	return strconv.Atoi(value)
}
func databases() (*sql.DB, *sql.DB, error) {
	pg, err := sql.Open("pgx", "postgres://app:local-app-only@postgres:5432/orders?sslmode=disable&connect_timeout=5")
	if err != nil {
		return nil, nil, err
	}
	dsn := "root@tcp(fe1:9030)/?timeout=5s&readTimeout=60s&writeTimeout=30s&interpolateParams=true&enable_sql_cache=false&enable_query_cache=false"
	if replica := os.Getenv("REPLICA_INDEX"); replica != "" {
		index, err := strconv.Atoi(replica)
		if err != nil || index < 0 || index > 2 {
			pg.Close()
			return nil, nil, fmt.Errorf("invalid fixed replica index")
		}
		dsn += "&skip_missing_version=false&fallback_other_replica_when_fixed_corrupt=false&use_fix_replica=" + replica
	}
	doris, err := sql.Open("mysql", dsn)
	if err != nil {
		pg.Close()
		return nil, nil, err
	}
	for _, db := range []*sql.DB{pg, doris} {
		db.SetMaxOpenConns(2)
		db.SetMaxIdleConns(2)
		db.SetConnMaxLifetime(5 * time.Minute)
	}
	return pg, doris, nil
}

func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("one operation required")
	}
	mode := os.Args[1]
	if value := os.Getenv("CASE_ID"); value != "" && !validRun.MatchString(value) {
		return fmt.Errorf("invalid CASE_ID")
	}
	if mode == "volumes" {
		return measureVolumes()
	}
	if mode == "cgroup" {
		return measureCgroup()
	}
	if mode == "idle" {
		for {
			time.Sleep(time.Hour)
		}
	}
	if mode != "setup" && mode != "drop" && !validRun.MatchString(os.Getenv("RUN_ID")) {
		return fmt.Errorf("owned RUN_ID required")
	}
	pg, doris, err := databases()
	if err != nil {
		return err
	}
	defer pg.Close()
	defer doris.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Minute)
	defer cancel()
	n, err := count("ORDER_COUNT", 1000000)
	if err != nil {
		return err
	}
	customers, err := count("CUSTOMER_COUNT", 10000)
	if err != nil {
		return err
	}
	if n < 10000 || n > 2000000 || customers < 100 || customers > 10000 || customers > n/2 {
		return fmt.Errorf("dataset bounds violated")
	}
	emit(map[string]any{"event": "configuration", "orders": n, "customers": customers, "seed": 42193, "pool_size": 2, "source_commit_timeout_ms": 30000, "source_mutation_retries": 0, "source_commit": "synchronous", "mysql_read_timeout_ms": 60000})
	switch mode {
	case "setup":
		for _, setting := range []string{"fsync", "full_page_writes", "synchronous_commit"} {
			if version(ctx, pg, "SHOW "+setting) != "on" {
				return fmt.Errorf("source durability setting %s is not on", setting)
			}
		}
		deadline := time.Now().Add(3 * time.Minute)
		for {
			var ready int
			err = doris.QueryRowContext(ctx, "SELECT count(*) FROM backends() WHERE Alive=true AND TotalCapacity>0 AND AvailCapacity>1073741824").Scan(&ready)
			if err == nil && ready == 3 {
				break
			}
			if time.Now().After(deadline) {
				return fmt.Errorf("three native backends not capacity-ready: %v", err)
			}
			time.Sleep(time.Second)
		}
		if _, err = doris.ExecContext(ctx, "CREATE DATABASE IF NOT EXISTS pipeline"); err != nil {
			return err
		}
		for _, table := range []string{"checkpointed", "asynchronous"} {
			query := "CREATE TABLE IF NOT EXISTS pipeline." + table + " (order_id BIGINT,customer_id BIGINT,amount_cents BIGINT,region VARCHAR(64),order_revision BIGINT,customer_revision BIGINT,event_second BIGINT,version BIGINT) UNIQUE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 8 PROPERTIES ('replication_num'='3','enable_unique_key_merge_on_write'='true','function_column.sequence_col'='version','compression'='ZSTD')"
			if _, err = doris.ExecContext(ctx, query); err != nil {
				return err
			}
		}
		for _, query := range []string{"CREATE USER IF NOT EXISTS 'pipeline' IDENTIFIED BY 'local-doris-only'", "GRANT SELECT_PRIV,LOAD_PRIV ON pipeline.* TO 'pipeline'"} {
			if _, err = doris.ExecContext(ctx, query); err != nil {
				return err
			}
		}
		emit(map[string]any{"event": "setup", "doris_version": version(ctx, doris, "SELECT @@version_comment"), "postgres_version": version(ctx, pg, "SHOW server_version")})
		return nil
	case "seed", "mutate", "dimensions", "delete":
		return source(ctx, pg, mode, os.Getenv("RUN_ID"), n, customers)
	case "verify-receipts":
		return receipts(ctx, pg)
	case "verify":
		return verify(ctx, pg, doris)
	case "reader", "query":
		return reader(ctx, doris, os.Getenv("RUN_ID"), n, customers, mode == "query")
	case "drop":
		_, err = doris.ExecContext(ctx, "DROP DATABASE IF EXISTS pipeline")
		return err
	default:
		return fmt.Errorf("unknown operation %q", mode)
	}
}
func version(ctx context.Context, db *sql.DB, query string) string {
	var result string
	if err := db.QueryRowContext(ctx, query).Scan(&result); err != nil {
		return "unavailable: " + err.Error()
	}
	return result
}
func main() {
	if source, err := os.ReadFile("/source.sha256"); err == nil {
		emit(map[string]any{"event": "source", "manifest": string(source)})
	}
	err := run()
	var usage syscall.Rusage
	if e := syscall.Getrusage(syscall.RUSAGE_SELF, &usage); e == nil {
		emit(map[string]any{"event": "resources", "peak_rss_kib": usage.Maxrss, "user_cpu_s": float64(usage.Utime.Sec) + float64(usage.Utime.Usec)/1e6, "system_cpu_s": float64(usage.Stime.Sec) + float64(usage.Stime.Usec)/1e6})
	}
	if err != nil {
		emit(map[string]any{"event": "fatal", "error": err.Error()})
		os.Exit(1)
	}
}
