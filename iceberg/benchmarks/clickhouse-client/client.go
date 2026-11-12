package clickhouseclient

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
	"github.com/ClickHouse/clickhouse-go/v2/lib/driver"
	_ "github.com/duckdb/duckdb-go/v2"
)

type Client struct {
	Conn  driver.Conn
	Input *sql.DB
}

func Open(addr string, threads int) (*Client, error) {
	conn, err := clickhouse.Open(&clickhouse.Options{
		Addr:        []string{addr},
		Protocol:    clickhouse.Native,
		Auth:        clickhouse.Auth{Database: "default", Username: "default", Password: "bench"},
		DialTimeout: 10 * time.Second,
		Settings: clickhouse.Settings{
			"max_threads":                           threads,
			"allow_insert_into_iceberg":             1,
			"mutations_sync":                        2,
			"allow_experimental_iceberg_compaction": 1,
		},
	})
	if err != nil {
		return nil, err
	}
	input, err := sql.Open("duckdb", "")
	if err != nil {
		conn.Close()
		return nil, err
	}
	input.SetMaxOpenConns(1)
	return &Client{Conn: conn, Input: input}, nil
}

func (c *Client) Exec(ctx context.Context, query string) error {
	return c.Conn.Exec(ctx, query)
}

func (c *Client) Query(ctx context.Context, query string) (driver.Rows, error) {
	return c.Conn.Query(ctx, query)
}

// InsertParquet reads deterministic workload input with DuckDB and streams
// rows through ClickHouse's native Go batch API.
func (c *Client) InsertParquet(ctx context.Context, table, path string) error {
	rows, err := c.Input.QueryContext(ctx, fmt.Sprintf("SELECT id, region, amount, version FROM read_parquet('%s')", path))
	if err != nil {
		return err
	}
	defer rows.Close()
	batch, err := c.Conn.PrepareBatch(ctx, "INSERT INTO "+table)
	if err != nil {
		return err
	}
	for rows.Next() {
		var id, version int64
		var region int32
		var amount float64
		if err := rows.Scan(&id, &region, &amount, &version); err != nil {
			return err
		}
		if err := batch.Append(id, region, amount, version); err != nil {
			return err
		}
	}
	if err := rows.Err(); err != nil {
		return err
	}
	return batch.Send()
}

func (c *Client) Version(ctx context.Context) (string, error) {
	var version string
	err := c.Conn.QueryRow(ctx, "SELECT version()").Scan(&version)
	return version, err
}

func (c *Client) Close() error {
	err := c.Conn.Close()
	return errors.Join(err, c.Input.Close())
}
