package workloadgenerator

import (
	"context"
	"database/sql"
	"fmt"
	"os"
	"path/filepath"

	_ "github.com/duckdb/duckdb-go/v2"
)

type Inputs struct {
	Seed          string
	DuckDBVersion string
	Appends       []string
	Updates       []string
}

func Generate(ctx context.Context, dir string, rows, appendRows, updateRows int64, batches int) (Inputs, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return Inputs{}, err
	}
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return Inputs{}, err
	}
	defer db.Close()
	if _, err = db.ExecContext(ctx, "SET threads = 4"); err != nil {
		return Inputs{}, err
	}
	var version string
	if err := db.QueryRowContext(ctx, "SELECT version()").Scan(&version); err != nil {
		return Inputs{}, err
	}
	inputs := Inputs{Seed: filepath.Join(dir, "seed.parquet"), DuckDBVersion: version}
	seedSQL := fmt.Sprintf("COPY (SELECT i::BIGINT AS id, (i %% 32)::INTEGER AS region, ((i %% 10000)::DOUBLE / 100.0) AS amount, 1::BIGINT AS version FROM range(%d) t(i)) TO '%s' (FORMAT PARQUET)", rows, inputs.Seed)
	if err := writeParquet(ctx, db, inputs.Seed, seedSQL); err != nil {
		return Inputs{}, err
	}
	for batch := range batches {
		if appendRows > 0 {
			path := filepath.Join(dir, fmt.Sprintf("append-%04d.parquet", batch))
			start := rows + int64(batch)*appendRows
			query := fmt.Sprintf("COPY (SELECT i::BIGINT AS id, (i %% 32)::INTEGER AS region, ((i %% 10000)::DOUBLE / 100.0) AS amount, 1::BIGINT AS version FROM range(%d, %d) t(i)) TO '%s' (FORMAT PARQUET)", start, start+appendRows, path)
			if err := writeParquet(ctx, db, path, query); err != nil {
				return Inputs{}, err
			}
			inputs.Appends = append(inputs.Appends, path)
		}
		if updateRows > 0 {
			path := filepath.Join(dir, fmt.Sprintf("updates-%04d.parquet", batch))
			stride := max(int64(1), rows/updateRows)
			version := batch + 2
			delta := float64(batch + 1)
			query := fmt.Sprintf("COPY (SELECT (i * %d)::BIGINT AS id, ((i * %d + 7) %% 32)::INTEGER AS region, (((i * %d) %% 10000)::DOUBLE / 100.0 + %.1f) AS amount, %d::BIGINT AS version FROM range(%d) t(i)) TO '%s' (FORMAT PARQUET)", stride, stride, stride, delta, version, updateRows, path)
			if err := writeParquet(ctx, db, path, query); err != nil {
				return Inputs{}, err
			}
			inputs.Updates = append(inputs.Updates, path)
		}
	}
	return inputs, nil
}

func writeParquet(ctx context.Context, db *sql.DB, path, query string) error {
	if err := os.Remove(path); err != nil && !os.IsNotExist(err) {
		return err
	}
	_, err := db.ExecContext(ctx, query)
	return err
}
