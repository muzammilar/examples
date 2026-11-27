package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("expected setup, seed, verify or drop")
	}
	db, err := sql.Open("mysql", "root@tcp(fe:9030)/?timeout=5s&readTimeout=120s&writeTimeout=120s")
	if err != nil {
		return err
	}
	defer db.Close()
	db.SetMaxOpenConns(2)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	switch os.Args[1] {
	case "setup":
		deadline := time.Now().Add(90 * time.Second)
		for {
			var ready int
			err := db.QueryRowContext(ctx, "SELECT COUNT(*) FROM backends() WHERE Alive = true AND TotalCapacity > 0 AND AvailCapacity > 1073741824").Scan(&ready)
			if err != nil {
				if strings.Contains(err.Error(), "[E-240]FE address is not available") && time.Now().Before(deadline) {
					time.Sleep(time.Second)
					continue
				}
				return err
			}
			if ready == 1 {
				break
			}
			if time.Now().After(deadline) {
				return fmt.Errorf("backend did not report usable capacity within 90 seconds")
			}
			time.Sleep(time.Second)
		}
		for _, query := range []string{
			"CREATE DATABASE IF NOT EXISTS lab",
			"CREATE TABLE IF NOT EXISTS lab.orders (order_id BIGINT, customer_id BIGINT, amount_cents BIGINT, revision BIGINT) UNIQUE KEY(order_id) DISTRIBUTED BY HASH(order_id) BUCKETS 8 PROPERTIES ('replication_num'='1', 'enable_unique_key_merge_on_write'='true', 'function_column.sequence_col'='revision')",
		} {
			if _, err := db.ExecContext(ctx, query); err != nil {
				return err
			}
		}
	case "seed":
		_, err := db.ExecContext(ctx, "INSERT INTO lab.orders SELECT number, number % 10000, 100 + number % 1000, 1 FROM numbers('number'='1000000')")
		return err
	case "drop":
		_, err := db.ExecContext(ctx, "DROP DATABASE IF EXISTS lab")
		return err
	case "verify":
		rows, err := db.QueryContext(ctx, "SELECT order_id, customer_id, amount_cents, revision FROM lab.orders ORDER BY order_id")
		if err != nil {
			return err
		}
		defer rows.Close()
		var count, total int64
		started := time.Now()
		for rows.Next() {
			var id, customer, amount, revision int64
			if err := rows.Scan(&id, &customer, &amount, &revision); err != nil {
				return err
			}
			if id != count || customer != id%10000 || amount != 100+id%1000 || revision != 1 {
				return fmt.Errorf("wrong order at position %d: %d %d %d %d", count, id, customer, amount, revision)
			}
			count++
			total += amount
		}
		if err := rows.Err(); err != nil {
			return err
		}
		if count != 1000000 || total != 599500000 {
			return fmt.Errorf("incomplete output: count=%d total=%d", count, total)
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{
			"orders": count, "amount_cents": total, "missing": 0, "wrong": 0,
			"fetch_verify_ms": float64(time.Since(started).Microseconds()) / 1000,
		})
	default:
		return fmt.Errorf("unknown command %q", os.Args[1])
	}
	return nil
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
