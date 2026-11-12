package clickhouseiceberg

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/example/iceberg-benchmark/clickhouse-client"
)

type Engine struct {
	Client    *clickhouseclient.Client
	Namespace string
	Rows      int64
	Warehouse string
	Path      string
	Table     string
}

func Open(addr, namespace string, rows int64, warehouse string, threads int) (*Engine, error) {
	client, err := clickhouseclient.Open(addr, threads)
	if err != nil {
		return nil, err
	}
	// ClickHouse restricts IcebergLocal paths to user_files. The named volume is
	// mounted there in this container and at warehouse in the benchmark runner.
	return &Engine{Client: client, Namespace: namespace, Rows: rows, Warehouse: warehouse, Path: "/var/lib/clickhouse/user_files/lake/" + namespace + "/clickhouse_events", Table: "bench_iceberg." + namespace + "_events"}, nil
}

func (e *Engine) Setup(ctx context.Context, seed string) error {
	sharedDir := e.Warehouse + "/" + e.Namespace
	if err := os.MkdirAll(sharedDir, 0777); err != nil {
		return err
	}
	if err := os.Chmod(sharedDir, 0777); err != nil {
		return err
	}
	if err := e.Client.Exec(ctx, "CREATE DATABASE IF NOT EXISTS bench_iceberg"); err != nil {
		return err
	}
	if err := e.Client.Exec(ctx, fmt.Sprintf("DROP TABLE IF EXISTS %s", e.Table)); err != nil {
		return err
	}
	if err := e.Client.Exec(ctx, fmt.Sprintf("CREATE TABLE %s (id Int64, region Int32, amount Float64, version Int64) ENGINE = IcebergLocal('%s')", e.Table, e.Path)); err != nil {
		return err
	}
	return e.Client.InsertParquet(ctx, e.Table, seed)
}

func (e *Engine) Append(ctx context.Context, parquet string) error {
	return e.Client.InsertParquet(ctx, e.Table, parquet)
}

func (e *Engine) Update(ctx context.Context, parquet string, stride int64) error {
	if err := e.Client.Exec(ctx, fmt.Sprintf("ALTER TABLE %s DELETE WHERE id < %d AND id %% %d = 0", e.Table, e.Rows, stride)); err != nil {
		return err
	}
	return e.Client.InsertParquet(ctx, e.Table, parquet)
}

func (e *Engine) Compact(ctx context.Context) (map[string]any, error) {
	measure := func(name, query string) (map[string]any, error) {
		start := time.Now()
		if err := e.Client.Exec(ctx, query); err != nil {
			return nil, err
		}
		return map[string]any{"name": name, "elapsed_ms": float64(time.Since(start).Microseconds()) / 1000}, nil
	}
	data, err := measure("apply_position_deletes", fmt.Sprintf("OPTIMIZE TABLE %s", e.Table))
	if err != nil {
		return nil, err
	}
	manifests, err := measure("rewrite_manifests", fmt.Sprintf("OPTIMIZE TABLE %s MANIFEST", e.Table))
	if err != nil {
		return nil, err
	}
	return map[string]any{"operations": []map[string]any{data, manifests}}, nil
}

func (e *Engine) Queries() map[string]string {
	return map[string]string{
		"full_scan": fmt.Sprintf("SELECT count() AS n, round(sum(amount), 2) AS amount, sum(version) AS versions FROM %s", e.Table),
		"group_by":  fmt.Sprintf("SELECT region, count() AS n, round(sum(amount), 2) AS amount FROM %s GROUP BY region ORDER BY region", e.Table),
		"selective": fmt.Sprintf("SELECT count() AS n, round(sum(amount), 2) AS amount FROM %s WHERE id >= %d AND id < %d", e.Table, e.Rows/2, e.Rows/2+max(int64(1), e.Rows/100)),
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
		var n uint64
		var versions int64
		var amount float64
		if name == "group_by" {
			var region int32
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
