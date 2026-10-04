// Failover load for RisingWave: one writer per table inserts batches with consecutive ids
// (retrying a failed batch until it succeeds; the primary key makes a retry idempotent) and a
// reader queries the MV. Prints one line per second and a summary; acknowledged ids are the
// ones whose INSERT returned without error.
package main

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func envInt(k string, d int) int {
	n, err := strconv.Atoi(env(k, strconv.Itoa(d)))
	if err != nil {
		panic(err)
	}
	return n
}

type stats struct {
	ok, fail, acked atomic.Int64
	firstFail       atomic.Int64 // unix ms
	lastFail        atomic.Int64
	resumed         atomic.Int64 // first success after the last failure
	maxBatchMs      atomic.Int64
	lastErr         atomic.Value // string
}

func (s *stats) failed(now int64, err error) {
	s.lastErr.Store(err.Error())
	s.fail.Add(1)
	s.firstFail.CompareAndSwap(0, now)
	s.lastFail.Store(now)
	s.resumed.Store(0)
}

func (s *stats) succeeded(now int64, d time.Duration) {
	s.ok.Add(1)
	if s.lastFail.Load() != 0 && s.resumed.Load() == 0 {
		s.resumed.Store(now)
	}
	ms := d.Milliseconds()
	for {
		cur := s.maxBatchMs.Load()
		if ms <= cur || s.maxBatchMs.CompareAndSwap(cur, ms) {
			break
		}
	}
}

func connect(ctx context.Context, url string, flush bool) (*pgx.Conn, error) {
	cfg, err := pgx.ParseConfig(url)
	if err != nil {
		return nil, err
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol
	cfg.ConnectTimeout = 3 * time.Second
	c, err := pgx.ConnectConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if flush {
		// INSERT returns only after the data is committed in a checkpoint.
		if _, err := c.Exec(ctx, "SET implicit_flush = true"); err != nil {
			c.Close(ctx)
			return nil, err
		}
	}
	return c, nil
}

func writer(ctx context.Context, url, table string, flush bool, batch int, s *stats) {
	var conn *pgx.Conn
	next := int64(0)
	for ctx.Err() == nil {
		if conn == nil {
			c, err := connect(ctx, url, flush)
			if err != nil {
				s.failed(time.Now().UnixMilli(), err)
				time.Sleep(200 * time.Millisecond)
				continue
			}
			conn = c
		}
		var sb strings.Builder
		fmt.Fprintf(&sb, "INSERT INTO %s (id, v) VALUES ", table)
		for i := 0; i < batch; i++ {
			if i > 0 {
				sb.WriteByte(',')
			}
			id := next + int64(i)
			fmt.Fprintf(&sb, "(%d,%d)", id, id%100)
		}
		qctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		start := time.Now()
		_, err := conn.Exec(qctx, sb.String())
		cancel()
		if err != nil {
			s.failed(time.Now().UnixMilli(), err)
			conn.Close(context.Background())
			conn = nil
			time.Sleep(200 * time.Millisecond)
			continue // retry the same ids
		}
		s.succeeded(time.Now().UnixMilli(), time.Since(start))
		next += int64(batch)
		s.acked.Store(next)
	}
	if conn != nil {
		conn.Close(context.Background())
	}
}

func reader(ctx context.Context, url, query string, s *stats) {
	var conn *pgx.Conn
	for ctx.Err() == nil {
		if conn == nil {
			c, err := connect(ctx, url, false)
			if err != nil {
				s.failed(time.Now().UnixMilli(), err)
				time.Sleep(200 * time.Millisecond)
				continue
			}
			conn = c
		}
		qctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		start := time.Now()
		var n int64
		err := conn.QueryRow(qctx, query).Scan(&n)
		cancel()
		if err != nil {
			s.failed(time.Now().UnixMilli(), err)
			conn.Close(context.Background())
			conn = nil
			time.Sleep(200 * time.Millisecond)
			continue
		}
		s.succeeded(time.Now().UnixMilli(), time.Since(start))
		time.Sleep(100 * time.Millisecond)
	}
}

func rel(ms, t0 int64) string {
	if ms == 0 {
		return "-"
	}
	return fmt.Sprintf("%.1fs", float64(ms-t0)/1000)
}

func main() {
	url := env("PGURL", "postgres://root@frontend:4566/dev?sslmode=disable")
	dur := time.Duration(envInt("DURATION", 120)) * time.Second
	batch := envInt("BATCH", 100)
	ctx, cancel := context.WithTimeout(context.Background(), dur)
	defer cancel()

	names := []string{"no_flush", "implicit_flush", "reader"}
	st := map[string]*stats{}
	for _, n := range names {
		st[n] = &stats{}
	}
	t0 := time.Now().UnixMilli()
	var wg sync.WaitGroup
	wg.Add(3)
	go func() { defer wg.Done(); writer(ctx, url, "events_no_flush", false, batch, st["no_flush"]) }()
	go func() { defer wg.Done(); writer(ctx, url, "events_flush", true, batch, st["implicit_flush"]) }()
	go func() { defer wg.Done(); reader(ctx, url, "SELECT n FROM events_no_flush_total", st["reader"]) }()

	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	prev := map[string][2]int64{}
	fmt.Println("t      | no_flush ok/err | implicit_flush ok/err | reader ok/err   (per second; ok = batches of", batch, "rows)")
loop:
	for {
		select {
		case <-ctx.Done():
			break loop
		case now := <-tick.C:
			line := fmt.Sprintf("%5.0fs", float64(now.UnixMilli()-t0)/1000)
			for _, n := range names {
				ok, fail := st[n].ok.Load(), st[n].fail.Load()
				p := prev[n]
				line += fmt.Sprintf(" | %6d/%-4d", ok-p[0], fail-p[1])
				prev[n] = [2]int64{ok, fail}
			}
			fmt.Println(line)
		}
	}
	wg.Wait()
	fmt.Println()
	fmt.Println("summary (times relative to start)")
	for _, n := range names {
		s := st[n]
		fmt.Printf("%-15s ok=%d failed_attempts=%d first_error=%s last_error=%s resumed=%s max_ok_latency=%dms",
			n, s.ok.Load(), s.fail.Load(), rel(s.firstFail.Load(), t0), rel(s.lastFail.Load(), t0), rel(s.resumed.Load(), t0), s.maxBatchMs.Load())
		if n != "reader" {
			fmt.Printf(" acked_rows=%d", s.acked.Load())
		}
		fmt.Println()
		if e, ok := s.lastErr.Load().(string); ok {
			if len(e) > 300 {
				e = e[:300] + "..."
			}
			fmt.Printf("%-15s last error: %s\n", "", strings.ReplaceAll(e, "\n", " "))
		}
	}
	// machine-readable for scripts/failover.sh
	fmt.Printf("ACKED no_flush=%d implicit_flush=%d\n", st["no_flush"].acked.Load(), st["implicit_flush"].acked.Load())
}
