// Append-only customer orders, independent reads, and an external durability oracle.
package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

type fixture struct {
	ID        string `json:"id"`
	Customers int    `json:"customers"`
}
type runConfig struct {
	Base      int64 `json:"base"`
	Reserved  int   `json:"reserved"`
	Minimum   int   `json:"minimum"`
	Batch     int   `json:"batch"`
	Customers int   `json:"customers"`
	Live      bool  `json:"live"`
}
type event struct {
	Stage        string    `json:"stage"`
	At           time.Time `json:"at"`
	Started      time.Time `json:"started"`
	Phase        string    `json:"phase"`
	First        int64     `json:"first"`
	Count        int       `json:"count"`
	Customer     int       `json:"customer"`
	Shard        int       `json:"shard"`
	Attempt      int       `json:"attempt,omitempty"`
	Latency      float64   `json:"latency_ms"`
	BatchLatency float64   `json:"batch_latency_ms,omitempty"`
	Ambiguous    bool      `json:"ambiguous,omitempty"`
	Outcome      string    `json:"outcome,omitempty"`
	Error        string    `json:"error,omitempty"`
}
type journal struct {
	file *os.File
	mu   sync.Mutex
}

func readJSON(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}
func save(path string, value any) error {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	if err = json.NewEncoder(f).Encode(value); err != nil {
		return err
	}
	return f.Sync()
}
func openJournal(name string) (*journal, error) {
	f, err := os.OpenFile(filepath.Join("/results", name), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	return &journal{file: f}, nil
}
func (j *journal) append(e event) error {
	j.mu.Lock()
	defer j.mu.Unlock()
	phase, err := os.ReadFile("/results/phase")
	if err != nil {
		return err
	}
	if e.At.IsZero() {
		e.At = time.Now().UTC()
	}
	e.Phase = strings.TrimSpace(string(phase))
	if err = json.NewEncoder(j.file).Encode(e); err != nil {
		return err
	}
	return j.file.Sync()
}
func records(path string, visit func(event) error) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	r := bufio.NewReader(f)
	for {
		line, err := r.ReadString('\n')
		if errors.Is(err, io.EOF) {
			if len(line) > 0 {
				return fmt.Errorf("truncated witness: %s", path)
			}
			return nil
		}
		if err != nil {
			return err
		}
		var e event
		if err = json.Unmarshal([]byte(line), &e); err != nil {
			return err
		}
		if err = visit(e); err != nil {
			return err
		}
	}
}
func stopped(name string) bool { _, err := os.Stat("/results/" + name); return err == nil }
func number(name string, fallback int) int {
	value := os.Getenv(name)
	if value == "" {
		return fallback
	}
	n, err := strconv.Atoi(value)
	if err != nil || n < 1 {
		log.Fatalf("invalid %s=%q", name, value)
	}
	return n
}
func main() {
	if len(os.Args) != 2 {
		log.Fatal("usage: workload seed|load|watch|verify")
	}
	var f fixture
	if err := readJSON("/witness/fixture.json", &f); err != nil {
		log.Fatal(err)
	}
	var route []int
	if err := readJSON("/witness/routing.json", &route); err != nil {
		log.Fatal(err)
	}
	if f.Customers < 1 || len(route) != f.Customers {
		log.Fatal("invalid native routing fixture")
	}
	for _, s := range route {
		if s < 0 || s > 2 {
			log.Fatal("invalid shard")
		}
	}
	db, err := sql.Open("pgx", os.Getenv("DSN"))
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()
	db.SetMaxOpenConns(number("WORKERS", 8))
	db.SetMaxIdleConns(number("WORKERS", 8))
	switch os.Args[1] {
	case "seed":
		for c := 0; c < f.Customers; c++ {
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			_, err = db.ExecContext(ctx, "INSERT INTO customers(customer_id,name) VALUES($1,$2) ON CONFLICT(customer_id) DO NOTHING", c, fmt.Sprintf("customer-%d", c))
			cancel()
			if err != nil {
				break
			}
		}
	case "load":
		var c runConfig
		err = readJSON("/results/run.json", &c)
		if err == nil {
			err = load(db, c, route)
		}
	case "watch":
		err = watch(db, f, route)
	case "verify":
		shard := -1
		if v := os.Getenv("EXPECTED_SHARD"); v != "" {
			shard, err = strconv.Atoi(v)
			if err != nil || shard < 0 || shard > 2 {
				log.Fatal("invalid EXPECTED_SHARD")
			}
		}
		err = verify(db, f, route, shard)
	default:
		err = errors.New("unknown workload mode")
	}
	if err != nil {
		log.Fatal(err)
	}
}
