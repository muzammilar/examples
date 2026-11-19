package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type database struct {
	hosts  []string
	pools  map[string]*pgxpool.Pool
	http   *http.Client
	single bool
}

func openDatabase(ctx context.Context) (*database, error) {
	d := &database{hosts: strings.Split(os.Getenv("PG_NODES"), ","), pools: make(map[string]*pgxpool.Pool), http: &http.Client{Timeout: 700 * time.Millisecond}, single: os.Getenv("MODE") == "single"}
	for _, h := range d.hosts {
		if h == "" {
			return nil, fmt.Errorf("empty database host")
		}
		config, err := pgxpool.ParseConfig("host=" + h + " user=postgres password=local-postgres dbname=postgres connect_timeout=2 sslmode=disable")
		if err != nil {
			return nil, err
		}
		config.MaxConns = 2
		config.ConnConfig.RuntimeParams["hnsw.ef_search"] = "256"
		config.ConnConfig.RuntimeParams["hnsw.iterative_scan"] = "strict_order"
		config.ConnConfig.RuntimeParams["hnsw.max_scan_tuples"] = "50000"
		config.ConnConfig.RuntimeParams["statement_timeout"] = "2500"
		config.ConnConfig.RuntimeParams["application_name"] = "inventory-" + os.Args[1]
		p, err := pgxpool.NewWithConfig(ctx, config)
		if err != nil {
			return nil, err
		}
		d.pools[h] = p
	}
	return d, nil
}
func (d *database) primary(ctx context.Context) (*pgxpool.Pool, string, error) {
	for _, h := range d.hosts {
		if d.single {
			return d.pools[h], h, nil
		}
		req, err := http.NewRequestWithContext(ctx, "GET", "http://"+h+":8008/primary", nil)
		if err != nil {
			return nil, "", err
		}
		response, err := d.http.Do(req)
		if err == nil {
			response.Body.Close()
			if response.StatusCode == 200 {
				return d.pools[h], h, nil
			}
		}
	}
	return nil, "", fmt.Errorf("no primary discovered; mutation not sent")
}
func run() error {
	if len(os.Args) != 2 || (os.Args[1] != "writer" && os.Args[1] != "reader") {
		return fmt.Errorf("expected writer or reader")
	}
	role := os.Args[1]
	root := "/results"
	duration, err := boundedInteger("DURATION_SECONDS", 1800, 60, 7200)
	if err != nil {
		return err
	}
	batch, err := boundedInteger("UPDATE_BATCH", 10, 1, 100)
	if err != nil {
		return err
	}
	fixture, err := loadFixture("/corpus")
	if err != nil {
		return err
	}
	var plan struct {
		StartVersion int `json:"start_version"`
	}
	data, err := os.ReadFile(filepath.Join(root, "case.json"))
	if err != nil {
		return err
	}
	if err = json.Unmarshal(data, &plan); err != nil {
		return err
	}
	if plan.StartVersion < 1 || plan.StartVersion > 100000000 {
		return fmt.Errorf("invalid version")
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(duration)*time.Second)
	defer cancel()
	db, err := openDatabase(ctx)
	if err != nil {
		return err
	}
	defer func() {
		for _, p := range db.pools {
			p.Close()
		}
	}()
	journal, err := openJournal(filepath.Join(root, role+".jsonl"))
	if err != nil {
		return err
	}
	if role == "writer" {
		err = writeInventory(ctx, root, fixture, db, journal, plan.StartVersion, batch)
	} else {
		err = readInventory(ctx, root, fixture, db, journal)
	}
	return errors.Join(err, journal.file.Close())
}
func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
