package main

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
)

type acknowledgment struct {
	First        int       `json:"first"`
	Count        int       `json:"count"`
	Customer     int       `json:"customer"`
	LatencyMS    float64   `json:"latency_ms"`
	Phase        string    `json:"phase"`
	Retries      int       `json:"retries"`
	CompletedAt  time.Time `json:"completed_at"`
	CommitErrors int       `json:"commit_errors"`
}
type phaseStats struct {
	Transactions int     `json:"transactions"`
	Records      int     `json:"records"`
	Retries      int     `json:"retries"`
	P50          float64 `json:"p50_ms"`
	P99          float64 `json:"p99_ms"`
	Max          float64 `json:"max_ms"`
	CommitErrors int     `json:"commit_errors"`
}

func number(key string, fallback int) int {
	s := os.Getenv(key)
	if s == "" {
		return fallback
	}
	n, e := strconv.Atoi(s)
	if e != nil || n < 1 {
		log.Fatalf("invalid %s=%q", key, s)
	}
	return n
}
func phase() string {
	b, e := os.ReadFile("/results/phase")
	if e != nil {
		return "baseline"
	}
	return strings.TrimSpace(string(b))
}
func queryContext() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 15*time.Second)
}
func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if len(os.Args) != 2 {
		log.Fatal("usage: workload seed|load|check")
	}
	db, e := sql.Open("pgx", os.Getenv("DSN"))
	if e != nil {
		log.Fatal(e)
	}
	defer db.Close()
	workers := number("WORKERS", 8)
	db.SetMaxOpenConns(workers)
	db.SetMaxIdleConns(workers)
	count, customers, batch := number("COUNT", 1000000), number("CUSTOMERS", 10000), number("BATCH", 100)
	var err error
	switch os.Args[1] {
	case "seed":
		err = seed(db, customers)
	case "load":
		err = load(db, count, customers, batch, workers)
	case "check":
		err = check(db, count, customers, batch)
	default:
		err = errors.New("unknown mode")
	}
	if err != nil {
		log.Fatal(err)
	}
}
func insertOrders(ctx context.Context, tx *sql.Tx, first, n, customer int) error {
	if os.Getenv("BULK") != "1" {
		for id := first; id < first+n; id++ {
			if _, err := tx.ExecContext(ctx, "INSERT INTO orders(customer_id,op_id,amount) VALUES($1,$2,$3) ON CONFLICT(customer_id,op_id) DO NOTHING", customer, id, id%10000+1); err != nil {
				return err
			}
		}
		return nil
	}
	var b strings.Builder
	b.WriteString("INSERT INTO orders(customer_id,op_id,amount) VALUES")
	args := make([]any, 0, n*3)
	for i := 0; i < n; i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "($%d,$%d,$%d)", i*3+1, i*3+2, i*3+3)
		id := first + i
		args = append(args, customer, id, id%10000+1)
	}
	b.WriteString(" ON CONFLICT(customer_id,op_id) DO NOTHING")
	_, err := tx.ExecContext(ctx, b.String(), args...)
	return err
}

func seed(db *sql.DB, customers int) error {
	for c := 0; c < customers; c++ {
		ctx, cancel := queryContext()
		_, e := db.ExecContext(ctx, "INSERT INTO customers(customer_id,name) VALUES($1,$2) ON CONFLICT(customer_id) DO UPDATE SET name=excluded.name", c, fmt.Sprintf("customer-%d", c))
		cancel()
		if e != nil {
			return e
		}
	}
	fmt.Printf("seeded %d customers\n", customers)
	return nil
}
func load(db *sql.DB, count, customers, batch, workers int) error {
	if e := os.MkdirAll("/results", 0755); e != nil {
		return e
	}
	f, e := os.OpenFile("/results/acknowledged.jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if e != nil {
		return fmt.Errorf("use a fresh results directory: %w", e)
	}
	defer f.Close()
	jobs := make(chan int)
	acks := make(chan acknowledgment, workers)
	errs := make(chan error, workers)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for first := range jobs {
				n := min(batch, count-first)
				customer := (first / batch) % customers
				started := time.Now()
				retries := 0
				commitErrors := 0
				for {
					if ctx.Err() != nil {
						return
					}
					qctx, qcancel := queryContext()
					tx, e := db.BeginTx(qctx, nil)
					if e == nil {
						e = insertOrders(qctx, tx, first, n, customer)
						if e == nil {
							e = tx.Commit()
							if e != nil {
								commitErrors++
							}
						} else {
							_ = tx.Rollback()
						}
					}
					qcancel()
					if e == nil {
						acks <- acknowledgment{First: first, Count: n, Customer: customer, LatencyMS: float64(time.Since(started).Microseconds()) / 1000, Phase: phase(), Retries: retries, CompletedAt: time.Now().UTC(), CommitErrors: commitErrors}
						break
					}
					retries++
					log.Printf("request_error first=%d customer=%d retry=%d commit_errors=%d error=%v", first, customer, retries, commitErrors, e)
					if time.Since(started) > 3*time.Minute {
						errs <- fmt.Errorf("batch %d failed after %d retries: %w", first, retries, e)
						cancel()
						return
					}
					select {
					case <-time.After(100 * time.Millisecond):
					case <-ctx.Done():
						return
					}
				}
			}
		}()
	}
	go func() {
		defer close(jobs)
		var ticks <-chan time.Time
		if os.Getenv("RATE") != "" && os.Getenv("RATE") != "0" {
			rate := number("RATE", 25000)
			interval := time.Duration(float64(time.Second) * float64(batch) / float64(rate))
			if interval < time.Microsecond {
				interval = time.Microsecond
			}
			ticker := time.NewTicker(interval)
			defer ticker.Stop()
			ticks = ticker.C
		}
		for first := 0; first < count; first += batch {
			if ticks != nil {
				select {
				case <-ticks:
				case <-ctx.Done():
					return
				}
			}
			select {
			case jobs <- first:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() { wg.Wait(); close(acks) }()
	enc := json.NewEncoder(f)
	samples := map[string][]float64{}
	stats := map[string]*phaseStats{}
	total := 0
	for a := range acks {
		if e = enc.Encode(a); e != nil {
			cancel()
			return e
		}
		if e = f.Sync(); e != nil {
			cancel()
			return e
		}
		total += a.Count
		if stats[a.Phase] == nil {
			stats[a.Phase] = &phaseStats{}
		}
		s := stats[a.Phase]
		s.Transactions++
		s.Records += a.Count
		s.Retries += a.Retries
		s.CommitErrors += a.CommitErrors
		samples[a.Phase] = append(samples[a.Phase], a.LatencyMS)
		if total%10000 == 0 {
			fmt.Printf("acknowledged=%d phase=%s\n", total, a.Phase)
		}
	}
	select {
	case e := <-errs:
		return e
	default:
	}
	if total != count {
		return fmt.Errorf("acknowledged %d, expected %d", total, count)
	}
	for p, v := range samples {
		sort.Float64s(v)
		stats[p].P50 = v[(len(v)-1)/2]
		stats[p].P99 = v[(len(v)-1)*99/100]
		stats[p].Max = v[len(v)-1]
	}
	result := struct {
		Acknowledged     int                    `json:"acknowledged"`
		ElapsedSeconds   float64                `json:"elapsed_seconds"`
		RecordsPerSecond float64                `json:"records_per_second"`
		Phases           map[string]*phaseStats `json:"phases"`
	}{total, time.Since(start).Seconds(), float64(total) / time.Since(start).Seconds(), stats}
	data, e := json.MarshalIndent(result, "", "  ")
	if e != nil {
		return e
	}
	fmt.Println(string(data))
	return os.WriteFile("/results/load.json", append(data, '\n'), 0644)
}
func check(db *sql.DB, count, customers, batch int) error {
	f, e := os.Open("/results/acknowledged.jsonl")
	if e != nil {
		return e
	}
	defer f.Close()
	ack := make([]bool, count)
	scan := bufio.NewScanner(f)
	ackCount := 0
	for scan.Scan() {
		var a acknowledgment
		if e = json.Unmarshal(scan.Bytes(), &a); e != nil {
			return e
		}
		if a.First < 0 || a.Count < 1 || a.First+a.Count > count {
			return errors.New("invalid witness batch")
		}
		for i := a.First; i < a.First+a.Count; i++ {
			if ack[i] {
				return fmt.Errorf("duplicate witness id %d", i)
			}
			ack[i] = true
			ackCount++
		}
	}
	if e = scan.Err(); e != nil {
		return e
	}
	seen := make([]bool, count)
	rows, e := db.Query("SELECT o.op_id,o.customer_id,o.amount FROM orders o JOIN customers c ON c.customer_id=o.customer_id")
	if e != nil {
		return e
	}
	defer rows.Close()
	found, duplicates, unacknowledged := 0, 0, 0
	var sum int64
	for rows.Next() {
		var id, c, amount int
		if e = rows.Scan(&id, &c, &amount); e != nil {
			return e
		}
		if id < 0 || id >= count {
			return fmt.Errorf("unexpected id %d", id)
		}
		if seen[id] {
			duplicates++
		}
		seen[id] = true
		if !ack[id] {
			unacknowledged++
		}
		if c != (id/batch)%customers || amount != id%10000+1 {
			return fmt.Errorf("incorrect values for id %d", id)
		}
		found++
		sum += int64(amount)
	}
	if e = rows.Err(); e != nil {
		return e
	}
	missing := 0
	for i, a := range ack {
		if a && !seen[i] {
			missing++
		}
	}
	data, e := json.MarshalIndent(map[string]any{"expected": count, "acknowledged": ackCount, "observed": found, "missing_acknowledged": missing, "duplicate_ids": duplicates, "committed_without_witness": unacknowledged, "amount_sum": sum}, "", "  ")
	if e != nil {
		return e
	}
	fmt.Println(string(data))
	if e = os.WriteFile(filepath.Join("/results", "check.json"), append(data, '\n'), 0644); e != nil {
		return e
	}
	if missing != 0 || duplicates != 0 || unacknowledged != 0 || found != count || ackCount != count {
		return errors.New("data reconciliation failed")
	}
	return nil
}
