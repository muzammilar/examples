package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/go-sql-driver/mysql"
)

var httpClient = &http.Client{
	Timeout:   90 * time.Second,
	Transport: &http.Transport{MaxIdleConnsPerHost: 4, MaxConnsPerHost: 4},
}

func emit(value any) {
	if err := json.NewEncoder(os.Stdout).Encode(value); err != nil {
		panic(err)
	}
}

func number(name string, fallback, minimum, maximum int64) (int64, error) {
	if os.Getenv(name) == "" {
		return fallback, nil
	}
	v, err := strconv.ParseInt(os.Getenv(name), 10, 64)
	if err != nil || v < minimum || v > maximum {
		return 0, fmt.Errorf("invalid %s", name)
	}
	return v, nil
}

func runID() (string, error) {
	id := os.Getenv("RUN_ID")
	if len(id) != 32 || strings.Trim(id, "0123456789abcdef") != "" {
		return "", errors.New("RUN_ID must be 32 lowercase hex characters")
	}
	return id, nil
}

func casePath() (string, error) {
	id, err := runID()
	if err != nil {
		return "", err
	}
	path := filepath.Join("/results/cases", id)
	return path, os.MkdirAll(path, 0755)
}

func exists(path string) bool { _, err := os.Stat(path); return err == nil }
func phase() string           { b, _ := os.ReadFile("/results/phase"); return strings.TrimSpace(string(b)) }

func source() (*sql.DB, error) {
	config, err := mysql.ParseDSN(os.Getenv("SOURCE_DSN"))
	if err != nil {
		return nil, err
	}
	if config.Net != "tcp" || config.Addr != "mysql:3306" || config.DBName != "orders" {
		return nil, errors.New("unexpected source endpoint")
	}
	config.Timeout, config.ReadTimeout, config.WriteTimeout = 5*time.Second, 60*time.Second, 30*time.Second
	db, err := sql.Open("mysql", config.FormatDSN())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(4)
	db.SetMaxIdleConns(4)
	db.SetConnMaxLifetime(5 * time.Minute)
	return db, nil
}

func run(ctx context.Context) error {
	if len(os.Args) != 2 {
		return errors.New("one mode required")
	}
	mode := os.Args[1]
	manifest, err := os.ReadFile("/source.sha256")
	if err != nil {
		return err
	}
	emit(map[string]any{"event": "source", "manifest": string(manifest)})
	if mode == "manifest" {
		return nil
	}
	if mode == "cgroup" {
		values := map[string]string{}
		for _, name := range []string{"cpu.stat", "memory.peak", "memory.current", "io.stat"} {
			data, err := os.ReadFile("/sys/fs/cgroup/" + name)
			if err != nil {
				values[name] = "unavailable: " + err.Error()
			} else {
				values[name] = strings.TrimSpace(string(data))
			}
		}
		emit(map[string]any{"event": "cgroup", "values": values})
		return nil
	}
	if mode == "idle" {
		<-ctx.Done()
		return nil
	}
	n, err := number("ORDER_COUNT", 1000000, 100000, 2000000)
	if err != nil {
		return err
	}
	c, err := number("CUSTOMER_COUNT", 10000, 1000, 10000)
	if err != nil {
		return err
	}
	emit(map[string]any{"event": "configuration", "mode": mode, "orders": n, "customers": c, "pool": 4, "connect_timeout_s": 5, "read_timeout_s": 60, "write_timeout_s": 30, "mutation_retries": 0, "http_timeout_s": 90})
	if mode == "fence" {
		return publishFence(ctx)
	}
	if mode == "probe-isolation" {
		return probeIsolation(ctx)
	}
	db, err := source()
	if err != nil {
		return err
	}
	defer db.Close()
	switch mode {
	case "seed":
		return seed(ctx, db, n, c)
	case "writer":
		return writer(ctx, db, n, c)
	case "reader":
		return reader(ctx, n, c)
	case "verify":
		return verify(ctx, db)
	case "request-backfill":
		return requestBackfill(ctx, db)
	case "wait-backfill":
		return waitBackfill(ctx)
	case "correct":
		return correct(ctx, db)
	default:
		return fmt.Errorf("unknown mode %q", mode)
	}
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	if err := run(ctx); err != nil {
		emit(map[string]any{"event": "fatal", "error": err.Error()})
		os.Exit(1)
	}
}
