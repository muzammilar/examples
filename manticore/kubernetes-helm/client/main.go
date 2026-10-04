// Load generator for the Manticore cluster examples (failover, scale-out / scale-in).
//
// Writers REPLACE batches of log lines into a replicated table (every node has every row)
// and a sharded table (rows spread over shards, rf copies each), round-robin over NODES.
// A failed batch is retried on the next node until it succeeds: REPLACE with the same ids is
// idempotent, so a retry after an ambiguous failure cannot duplicate rows. Readers run
// full-text + filter queries on both tables. Every INTERVAL a line with docs/s, queries/s,
// errors and read p99 is printed. At the end the acknowledged rows are counted on every
// node in VERIFY_NODES: lost = acknowledged - found must be 0.
package main

import (
	"context"
	"database/sql"
	"fmt"
	"math/rand"
	"os"
	"os/signal"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	v, err := strconv.Atoi(env(k, strconv.Itoa(def)))
	if err != nil {
		fmt.Fprintf(os.Stderr, "bad %s: %v\n", k, err)
		os.Exit(2)
	}
	return v
}

var (
	words    = strings.Fields("error timeout refused upstream connect reset denied slow retry cache miss hit login logout payment order cart checkout search index user admin session token expired invalid gateway backend database query lock deadlock commit rollback disk full memory pressure kernel panic restart deploy rollout canary healthy unhealthy")
	services = []string{"api", "auth", "billing", "cart", "search", "gateway", "worker", "scheduler"}
	levels   = []string{"debug", "info", "info", "info", "warn", "error"}
)

type stats struct {
	docs, writeErr, reads, readErr atomic.Int64
	mu                             sync.Mutex
	lat                            []float64
}

func (s *stats) addLat(ms float64) {
	s.mu.Lock()
	s.lat = append(s.lat, ms)
	s.mu.Unlock()
}

func (s *stats) takeP99() float64 {
	s.mu.Lock()
	l := s.lat
	s.lat = nil
	s.mu.Unlock()
	if len(l) == 0 {
		return 0
	}
	sort.Float64s(l)
	return l[int(float64(len(l)-1)*0.99)]
}

func open(node string) *sql.DB {
	// interpolateParams: Manticore has no server-side prepared statements over the MySQL protocol
	db, err := sql.Open("mysql", fmt.Sprintf("tcp(%s)/?interpolateParams=true&timeout=2s&readTimeout=10s&writeTimeout=10s", node))
	if err != nil {
		panic(err)
	}
	db.SetMaxOpenConns(64)
	db.SetMaxIdleConns(64)
	db.SetConnMaxLifetime(30 * time.Second)
	return db
}

func batchSQL(table string, first int64, n int, r *rand.Rand) string {
	var b strings.Builder
	fmt.Fprintf(&b, "REPLACE INTO %s (id, message, service, level, status, latency_ms, ts) VALUES ", table)
	now := time.Now().Unix()
	for i := 0; i < n; i++ {
		if i > 0 {
			b.WriteByte(',')
		}
		msg := make([]string, 6+r.Intn(10))
		for j := range msg {
			msg[j] = words[r.Intn(len(words))]
		}
		status := 200
		if r.Intn(20) == 0 {
			status = 500 + r.Intn(5)
		}
		fmt.Fprintf(&b, "(%d,'%s','%s','%s',%d,%d,%d)", first+int64(i), strings.Join(msg, " "),
			services[r.Intn(len(services))], levels[r.Intn(len(levels))], status, 1+r.Intn(2000), now)
	}
	return b.String()
}

type target struct {
	name     string // statement target (c:logs for a replicated table)
	countSQL string
	acked    atomic.Int64
}

func main() {
	nodes := strings.Split(env("NODES", "manticore-1:9306,manticore-2:9306,manticore-3:9306"), ",")
	verifyNodes := strings.Split(env("VERIFY_NODES", strings.Join(nodes, ",")), ",")
	writers := envInt("WRITERS", 4)
	readers := envInt("READERS", 4)
	batch := envInt("BATCH", 200)
	duration := time.Duration(envInt("DURATION", 60)) * time.Second
	interval := time.Duration(envInt("INTERVAL", 2)) * time.Second
	rate := envInt("RATE", 0) // total docs/s per table over all writers; 0 = as fast as possible
	cluster := env("CLUSTER", "c")
	var targets []*target
	if t := env("REPLICATED_TABLE", "logs"); t != "-" {
		targets = append(targets, &target{name: cluster + ":" + t, countSQL: "SELECT COUNT(*) FROM " + t})
	}
	if t := env("SHARDED_TABLE", "events"); t != "-" {
		targets = append(targets, &target{name: t, countSQL: "SELECT COUNT(*) FROM " + t})
	}
	dbs := make([]*sql.DB, len(nodes))
	for i, n := range nodes {
		dbs[i] = open(n)
	}
	// ids: a fresh range per run ((epoch seconds mod 1e5) * 1e9); writer w writes every writers-th batch
	base := time.Now().Unix() % 100000 * 1_000_000_000

	ctx, cancel := context.WithTimeout(context.Background(), duration)
	defer cancel()
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	go func() { <-sig; cancel() }()

	var st stats
	var wg sync.WaitGroup
	start := time.Now()
	fmt.Printf("load: nodes=%s writers=%d (batch %d, rate %d docs/s, 0 = unlimited) readers=%d duration=%s tables=", strings.Join(nodes, ","), writers, batch, rate, readers, duration)
	for _, t := range targets {
		fmt.Printf("%s ", t.name)
	}
	fmt.Println()

	for w := 0; w < writers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			r := rand.New(rand.NewSource(int64(w)))
			node := w
			for k := int64(0); ctx.Err() == nil; k++ {
				if rate > 0 { // pace: this writer sends one batch every writers*batch/rate seconds
					due := start.Add(time.Duration(float64(k*int64(writers)+int64(w)) * float64(batch) / float64(rate) * float64(time.Second)))
					if d := time.Until(due); d > 0 {
						select {
						case <-time.After(d):
						case <-ctx.Done():
						}
						if ctx.Err() != nil {
							break
						}
					}
				}
				first := base + (k*int64(writers)+int64(w))*int64(batch) + 1
				for _, t := range targets {
					q := batchSQL(t.name, first, batch, r)
					// retry on the next node until it succeeds; give up only 30 s after the run ended
					for attempt := 0; ; attempt++ {
						db := dbs[node%len(dbs)]
						c, cc := context.WithTimeout(context.Background(), 10*time.Second)
						_, err := db.ExecContext(c, q)
						cc()
						if err == nil {
							t.acked.Add(int64(batch))
							st.docs.Add(int64(batch))
							break
						}
						st.writeErr.Add(1)
						if attempt < 3 || attempt%20 == 0 {
							fmt.Printf("  [%6.1fs] write error on %s (attempt %d): %v\n", time.Since(start).Seconds(), nodes[node%len(nodes)], attempt+1, err)
						}
						node++
						if time.Since(start) > duration+30*time.Second {
							fmt.Printf("  giving up on batch at id %d\n", first)
							return
						}
						time.Sleep(100 * time.Millisecond)
					}
				}
				node++
			}
		}(w)
	}
	for i := 0; i < readers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			r := rand.New(rand.NewSource(int64(1000 + i)))
			for n := i; ctx.Err() == nil; n++ {
				t := targets[n%len(targets)]
				table := t.name[strings.Index(t.name, ":")+1:]
				q := fmt.Sprintf("SELECT service, COUNT(*) c FROM %s WHERE MATCH('%s %s') AND status >= 500 GROUP BY service ORDER BY c DESC LIMIT 5",
					table, words[r.Intn(len(words))], words[r.Intn(len(words))])
				c, cc := context.WithTimeout(context.Background(), 10*time.Second)
				t0 := time.Now()
				rows, err := dbs[n%len(dbs)].QueryContext(c, q)
				if err == nil {
					for rows.Next() {
					}
					err = rows.Err()
					rows.Close()
				}
				cc()
				if err != nil {
					if st.readErr.Add(1) <= 10 {
						fmt.Printf("  [%6.1fs] read error on %s: %v\n", time.Since(start).Seconds(), nodes[n%len(dbs)], err)
					}
					time.Sleep(100 * time.Millisecond)
					continue
				}
				st.reads.Add(1)
				st.addLat(float64(time.Since(t0).Microseconds()) / 1000)
			}
		}(i)
	}

	done := make(chan struct{})
	go func() { wg.Wait(); close(done) }()
	tick := time.NewTicker(interval)
	var lastDocs, lastReads, lastWE, lastRE int64
	last := start
	report := func() {
		now := time.Now()
		d, rd, we, re := st.docs.Load(), st.reads.Load(), st.writeErr.Load(), st.readErr.Load()
		sec := now.Sub(last).Seconds()
		fmt.Printf("[%6.1fs] %s writes %8.0f docs/s  write_errors %3d | reads %7.0f q/s  read_errors %3d  read_p99 %7.1f ms\n",
			now.Sub(start).Seconds(), now.UTC().Format("15:04:05"), float64(d-lastDocs)/sec, we-lastWE, float64(rd-lastReads)/sec, re-lastRE, st.takeP99())
		lastDocs, lastReads, lastWE, lastRE, last = d, rd, we, re, now
	}
loop:
	for {
		select {
		case <-tick.C:
			report()
		case <-done:
			break loop
		}
	}
	report()
	el := time.Since(start).Seconds()
	fmt.Printf("total: %d docs acknowledged (%.0f docs/s), %d write errors (all retried), %d queries (%.0f q/s), %d read errors\n",
		st.docs.Load(), float64(st.docs.Load())/el, st.writeErr.Load(), st.reads.Load(), float64(st.reads.Load())/el, st.readErr.Load())

	// verification: rows with ids of this run on every node must equal the acknowledged count
	fail := false
	for _, t := range targets {
		table := t.name[strings.Index(t.name, ":")+1:]
		for _, n := range verifyNodes {
			db := open(n)
			var found int64
			var err error
			for i := 0; i < 30; i++ { // replicated writes are applied asynchronously on other nodes: wait up to 30 s
				err = db.QueryRow(fmt.Sprintf("SELECT COUNT(*) FROM %s WHERE id > %d", table, base)).Scan(&found)
				if err == nil && found >= t.acked.Load() {
					break
				}
				time.Sleep(time.Second)
			}
			db.Close()
			if err != nil {
				fmt.Printf("verify %-7s on %-16s ERROR %v\n", table, n, err)
				fail = true
				continue
			}
			lost := t.acked.Load() - found
			if lost < 0 {
				lost = 0
			}
			fmt.Printf("verify %-7s on %-16s acknowledged %8d found %8d lost %d\n", table, n, t.acked.Load(), found, lost)
			if found != t.acked.Load() {
				fail = true
			}
		}
	}
	if fail {
		fmt.Println("VERIFY FAILED")
		os.Exit(1)
	}
	fmt.Println("VERIFY OK")
}
