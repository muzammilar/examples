package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	_ "github.com/duckdb/duckdb-go/v2"
)

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
func quote(s string) string             { return "'" + strings.ReplaceAll(s, "'", "''") + "'" }
func duck(attach bool) (*sql.DB, error) { return duckAt("", attach) }
func duckAt(path string, attach bool) (*sql.DB, error) {
	db, err := sql.Open("duckdb", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	statements := []string{"SET threads=2", "SET memory_limit='1GB'", "SET extension_directory='/opt/duckdb/extensions'", "LOAD httpfs", "LOAD iceberg"}
	if attach {
		statements = append(statements,
			"CREATE SECRET s3_secret (TYPE S3, KEY_ID 'demo', SECRET 'demo', ENDPOINT "+quote(env("S3_ENDPOINT", "storage:8333"))+", URL_STYLE 'path', USE_SSL false)",
			"CREATE SECRET catalog_secret (TYPE ICEBERG, ENDPOINT "+quote(env("CATALOG_URL", "http://storage:8181"))+", CLIENT_ID 'demo', CLIENT_SECRET 'demo')",
			"ATTACH 's3://warehouse' AS lake (TYPE iceberg, SECRET catalog_secret, ENDPOINT "+quote(env("CATALOG_URL", "http://storage:8181"))+", READ_ONLY false)")
	}
	for _, q := range statements {
		if _, err = db.Exec(q); err != nil {
			db.Close()
			return nil, fmt.Errorf("DuckDB %s: %w", q, err)
		}
	}
	return db, nil
}
func ch() (clickhouse.Conn, error) { return chAt(env("CLICKHOUSE_ADDR", "clickhouse:9000")) }
func chAt(addr string) (clickhouse.Conn, error) {
	return clickhouse.Open(&clickhouse.Options{Addr: []string{addr}, Auth: clickhouse.Auth{Database: "default", Username: "default", Password: env("CLICKHOUSE_PASSWORD", "local-reader")}, Settings: clickhouse.Settings{"allow_database_iceberg": 1, "allow_insert_into_iceberg": 1, "max_execution_time": 120, "max_memory_usage": 768 << 20, "max_threads": 2, "max_rows_to_read": 100000000, "max_result_rows": 10000}, DialTimeout: 5 * time.Second})
}
func probe() error {
	db, err := duck(true)
	if err != nil {
		return err
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	for _, q := range []string{"CREATE SCHEMA IF NOT EXISTS lake.lab", "DROP TABLE IF EXISTS lake.lab.probe", "CREATE TABLE lake.lab.probe (event_id BIGINT, customer_id INTEGER, amount_cents BIGINT)", "DROP TABLE IF EXISTS lake.lab.reverse", "CREATE TABLE lake.lab.reverse (event_id BIGINT, customer_id INTEGER, amount_cents BIGINT)", "INSERT INTO lake.lab.probe SELECT i,CAST(i%100 AS INTEGER),i%10000+1 FROM range(10000) t(i)"} {
		if _, err = db.ExecContext(ctx, q); err != nil {
			return fmt.Errorf("DuckDB %s: %w", q, err)
		}
	}
	reader, err := ch()
	if err != nil {
		return err
	}
	defer reader.Close()
	// Discover the fresh local reader before creating the shared catalog database.
	rows, err := reader.Query(ctx, "SELECT name FROM system.databases WHERE name NOT IN ('system','information_schema','INFORMATION_SCHEMA') LIMIT 100")
	if err != nil {
		return err
	}
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			rows.Close()
			return err
		}
		fmt.Println("database", name)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	rows, err = reader.Query(ctx, "SELECT name,value FROM system.settings WHERE name LIKE '%iceberg%' OR name LIKE '%data_lake%' LIMIT 100")
	if err != nil {
		return err
	}
	for rows.Next() {
		var n, v string
		if err = rows.Scan(&n, &v); err != nil {
			rows.Close()
			return err
		}
		fmt.Println(n, v)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return err
	}
	ddl := "CREATE DATABASE IF NOT EXISTS lake ENGINE=DataLakeCatalog(" + quote(env("CATALOG_URL", "http://storage:8181")) + ", 'demo', 'demo') SETTINGS catalog_type='rest', catalog_credential='demo:demo', vended_credentials=false, warehouse='warehouse', storage_endpoint='http://storage:8333/warehouse'"
	if err = reader.Exec(ctx, ddl); err != nil {
		return fmt.Errorf("catalog DDL: %w", err)
	}
	var n uint64
	var sum int64
	if err = reader.QueryRow(ctx, "SELECT count(),sum(amount_cents) FROM lake.`lab.probe` LIMIT 1").Scan(&n, &sum); err != nil {
		return err
	}
	if n != 10000 || sum != 50005000 {
		return fmt.Errorf("ClickHouse saw %d rows / %d cents", n, sum)
	}
	if err = reader.Exec(ctx, "INSERT INTO lake.`lab.reverse` SELECT toInt64(number),CAST(number%100 AS Int32),CAST(number%10000+1 AS Int64) FROM numbers(20000)"); err != nil {
		return fmt.Errorf("ClickHouse catalog append: %w", err)
	}
	// Reattach for an explicit fresh catalog view, rather than assuming cache invalidation.
	if _, err = db.ExecContext(ctx, "DETACH lake"); err != nil {
		return err
	}
	db.Close()
	db, err = duck(true)
	if err != nil {
		return err
	}
	defer db.Close()
	var count, total int64
	if err = db.QueryRowContext(ctx, "SELECT count(*),sum(amount_cents) FROM lake.lab.reverse").Scan(&count, &total); err != nil {
		return err
	}
	if count != 20000 || total != 100010000 {
		return fmt.Errorf("DuckDB reverse read: %d rows / %d cents", count, total)
	}
	fmt.Printf("PROBE PASS: %d rows, %d cents visible in both directions\n", count, total)
	return nil
}
func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if err := validateLocalTargets(os.Getenv); err != nil {
		log.Fatal(err)
	}
	if len(os.Args) != 2 {
		log.Fatal("usage: lab extensions|probe|load|verify")
	}
	mode := os.Args[1]
	var err error
	if mode == "extensions" || strings.HasPrefix(mode, "recovery-") {
		err = runCommand(mode)
	} else {
		err = measureCommand(mode, "/results", func() error { return runCommand(mode) })
	}
	if err != nil {
		log.Fatal(err)
	}
}

func runCommand(mode string) error {
	var err error
	switch mode {
	case "recovery-seed":
		err = recoverySeed()
	case "recovery-write":
		err = recoveryWriter()
	case "recovery-read":
		err = recoveryReader()
	case "recovery-verify":
		err = recoveryVerify()
	case "extensions":
		var db *sql.DB
		db, err = sql.Open("duckdb", "")
		if err == nil {
			defer db.Close()
			for _, q := range []string{"SET extension_directory='/opt/duckdb/extensions'", "FORCE INSTALL httpfs FROM 'https://extensions.duckdb.org'", "FORCE INSTALL avro FROM 'https://extensions.duckdb.org'", "FORCE INSTALL iceberg FROM 'https://extensions.duckdb.org'", "LOAD iceberg"} {
				if _, err = db.Exec(q); err != nil {
					break
				}
			}
			if err == nil {
				var installed int
				err = db.QueryRow("SELECT count(*) FROM duckdb_extensions() WHERE installed AND ((extension_name='httpfs' AND extension_version='827222f') OR (extension_name='avro' AND extension_version='f9d5902') OR (extension_name='iceberg' AND extension_version='45163a28'))").Scan(&installed)
				if err == nil && installed != 3 {
					err = fmt.Errorf("extension revisions differ from the qualified HTTPFS/Avro/Iceberg pins: matched %d/3", installed)
				}
			}
		}
	case "benchmark":
		err = benchmark()
	case "duck-archive":
		err = duckArchive(false)
	case "duck-archive-resume":
		err = duckArchive(true)
	case "native":
		err = native(false)
	case "native-stage":
		err = native(true)
	case "native-recover":
		err = recoverNative()
	case "append-source":
		err = appendSource()
	case "load":
		err = load()
	case "verify":
		err = verify()
	case "probe":
		err = probe()
	default:
		err = fmt.Errorf("unknown mode %s", mode)
	}
	return err
}
