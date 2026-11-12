package clickhousenative

import (
	"context"
	"fmt"
	"time"

	"github.com/example/iceberg-benchmark/clickhouse-client"
)

type Engine struct {
	Client *clickhouseclient.Client
	Rows   int64
	Table  string
}

func Open(addr, table string, rows int64, threads int) (*Engine, error) {
	client, err := clickhouseclient.Open(addr, threads)
	if err != nil {
		return nil, err
	}
	return &Engine{Client: client, Rows: rows, Table: "bench_native." + table}, nil
}

func (e *Engine) Setup(ctx context.Context, seed string) error {
	if err := e.Client.Exec(ctx, "CREATE DATABASE IF NOT EXISTS bench_native"); err != nil {
		return err
	}
	if err := e.Client.Exec(ctx, fmt.Sprintf("DROP TABLE IF EXISTS %s", e.Table)); err != nil {
		return err
	}
	if err := e.Client.Exec(ctx, fmt.Sprintf("CREATE TABLE %s (id UInt64, region UInt32, amount Float64, version UInt64) ENGINE = ReplacingMergeTree(version) ORDER BY id", e.Table)); err != nil {
		return err
	}
	return e.Client.InsertParquet(ctx, e.Table, seed)
}

func (e *Engine) Append(ctx context.Context, parquet string) error {
	return e.Client.InsertParquet(ctx, e.Table, parquet)
}

func (e *Engine) Update(ctx context.Context, parquet string, _ int64) error {
	return e.Client.InsertParquet(ctx, e.Table, parquet)
}

func (e *Engine) Compact(ctx context.Context) (map[string]any, error) {
	start := time.Now()
	if err := e.Client.Exec(ctx, fmt.Sprintf("OPTIMIZE TABLE %s FINAL", e.Table)); err != nil {
		return nil, err
	}
	return map[string]any{"operations": []map[string]any{{
		"name": "merge_parts_and_deduplicate", "elapsed_ms": float64(time.Since(start).Microseconds()) / 1000,
	}}}, nil
}

func (e *Engine) Queries() map[string]string {
	table := e.Table + " FINAL"
	return map[string]string{
		"full_scan": fmt.Sprintf("SELECT count() AS n, round(sum(amount), 2) AS amount, sum(version) AS versions FROM %s", table),
		"group_by":  fmt.Sprintf("SELECT region, count() AS n, round(sum(amount), 2) AS amount FROM %s GROUP BY region ORDER BY region", table),
		"selective": fmt.Sprintf("SELECT count() AS n, round(sum(amount), 2) AS amount FROM %s WHERE id >= %d AND id < %d", table, e.Rows/2, e.Rows/2+max(int64(1), e.Rows/100)),
	}
}

func (e *Engine) Query(ctx context.Context, name, query string) ([]map[string]any, error) {
	rows, err := e.Client.Query(ctx, query)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make([]map[string]any, 0)
	for rows.Next() {
		var n, versions uint64
		var amount float64
		if name == "group_by" {
			var region uint32
			if err := rows.Scan(&region, &n, &amount); err != nil {
				return nil, err
			}
			result = append(result, map[string]any{"region": region, "n": n, "amount": amount})
		} else if name == "full_scan" {
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

func (e *Engine) Version(ctx context.Context) (string, error) { return e.Client.Version(ctx) }
func (e *Engine) Close() error                                { return e.Client.Close() }
