package duckdbiceberg

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "github.com/duckdb/duckdb-go/v2"
)

type Engine struct {
	DB        *sql.DB
	Namespace string
	Rows      int64
	Path      string
	Table     string
}

func Open(namespace string, rows int64, warehouse string) (*Engine, error) {
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	endpoint := os.Getenv("ICEBERG_REST_URL")
	if endpoint == "" {
		endpoint = "http://rest:8181"
	}
	for _, query := range []string{"SET threads = 4", "SET memory_limit = '4.8GB'", "SET extension_directory = '/tmp/duckdb-extensions'", "INSTALL iceberg", "LOAD iceberg", fmt.Sprintf("ATTACH 'warehouse' AS lake (TYPE iceberg, ENDPOINT '%s', AUTHORIZATION_TYPE 'none')", endpoint), fmt.Sprintf("CREATE SCHEMA IF NOT EXISTS lake.%s", namespace), fmt.Sprintf("DROP TABLE IF EXISTS lake.%s.duckdb_events", namespace)} {
		if _, err := db.Exec(query); err != nil {
			db.Close()
			return nil, err
		}
	}
	path := filepath.Join(warehouse, namespace, "duckdb_events")
	return &Engine{DB: db, Namespace: namespace, Rows: rows, Path: path, Table: "lake." + namespace + ".duckdb_events"}, nil
}

func (e *Engine) Setup(ctx context.Context, seed string) error {
	metadataPath := filepath.Join(e.Path, "metadata")
	if err := os.MkdirAll(metadataPath, 0o777); err != nil {
		return err
	}
	if err := os.Chmod(metadataPath, 0o777); err != nil {
		return err
	}
	_, err := e.DB.ExecContext(ctx, fmt.Sprintf("CREATE TABLE %s AS SELECT * FROM read_parquet('%s')", e.Table, seed))
	return err
}

func (e *Engine) Append(ctx context.Context, parquet string) error {
	_, err := e.DB.ExecContext(ctx, fmt.Sprintf("INSERT INTO %s SELECT * FROM read_parquet('%s')", e.Table, parquet))
	return err
}

func (e *Engine) Update(ctx context.Context, parquet string, _ int64) error {
	query := fmt.Sprintf("MERGE INTO %s AS target USING read_parquet('%s') AS source ON target.id = source.id WHEN MATCHED THEN UPDATE SET region=source.region, amount=source.amount, version=source.version WHEN NOT MATCHED THEN INSERT (id, region, amount, version) VALUES (source.id, source.region, source.amount, source.version)", e.Table, parquet)
	_, err := e.DB.ExecContext(ctx, query)
	return err
}

func (e *Engine) Compact(ctx context.Context) (map[string]any, error) {
	start := time.Now()
	rows, err := e.DB.QueryContext(ctx, fmt.Sprintf("CALL iceberg_rewrite_data_files('%s', min_input_files := 1)", e.Table))
	if err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "iceberg_rewrite_data_files") && strings.Contains(strings.ToLower(err.Error()), "does not exist") {
			return map[string]any{"status": "unavailable", "reason": "iceberg_rewrite_data_files is not present in the pinned DuckDB Iceberg extension"}, nil
		}
		return nil, err
	}
	defer rows.Close()
	var rewritten, added, bytes, failed, removedDeletes int64
	if !rows.Next() {
		return nil, rows.Err()
	}
	if err := rows.Scan(&rewritten, &added, &bytes, &failed, &removedDeletes); err != nil {
		return nil, err
	}
	return map[string]any{
		"status": "completed",
		"data_file_rewrite_ms": float64(time.Since(start).Microseconds()) / 1000,
		"rewritten_data_files": rewritten, "added_data_files": added,
		"rewritten_bytes": bytes, "failed_data_files": failed,
		"removed_delete_files": removedDeletes,
	}, nil
}

func (e *Engine) Queries() map[string]string {
	return map[string]string{
		"full_scan": fmt.Sprintf("SELECT count(*) AS n, round(sum(amount), 2) AS amount, sum(version) AS versions FROM %s", e.Table),
		"group_by":  fmt.Sprintf("SELECT region, count(*) AS n, round(sum(amount), 2) AS amount FROM %s GROUP BY region ORDER BY region", e.Table),
		"selective": fmt.Sprintf("SELECT count(*) AS n, round(sum(amount), 2) AS amount FROM %s WHERE id >= %d AND id < %d", e.Table, e.Rows/2, e.Rows/2+max(int64(1), e.Rows/100)),
	}
}

func (e *Engine) Query(ctx context.Context, name, query string) ([]map[string]any, error) {
	rows, err := e.DB.QueryContext(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]map[string]any, 0)
	for rows.Next() {
		var n int64
		var amount float64
		if name == "group_by" {
			var region int32
			if err := rows.Scan(&region, &n, &amount); err != nil {
				return nil, err
			}
			result = append(result, map[string]any{"region": region, "n": n, "amount": amount})
		} else if name == "full_scan" {
			var versions int64
			if err := rows.Scan(&n, &amount, &versions); err != nil {
				return nil, err
			}
			result = append(result, map[string]any{"n": n, "amount": amount, "versions": versions})
		} else {
			if err := rows.Scan(&n, &amount); err != nil {
				return nil, err
			}
			result = append(result, map[string]any{"n": n, "amount": amount})
		}
	}
	return result, rows.Err()
}

func (e *Engine) Version(ctx context.Context) (string, error) {
	var version string
	err := e.DB.QueryRowContext(ctx, "SELECT version()").Scan(&version)
	return version, err
}

func (e *Engine) Close() error { return e.DB.Close() }
