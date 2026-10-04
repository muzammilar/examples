// Load generator and checker for the Memgraph Helm example (copied from ../../docker-compose-cluster/client,
// without the read-scaling mode).
//
//	client write  : WORKERS goroutines write (:Tick {seq}) nodes, one per auto-commit query, to
//	                whichever host in HOSTS accepts writes (the MAIN). A failed write is retried
//	                with the same seq on the next host. Every acknowledged seq is appended to
//	                ACKED_FILE. Prints writes/s per second, failures and the longest gap.
//	client verify : reads ACKED_FILE and, for every host in HOSTS, counts acknowledged seqs that
//	                are missing and seqs present that were never acknowledged.
//	client role   : prints the replication role and :Tick count of every host.
package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
	"github.com/neo4j/neo4j-go-driver/v6/neo4j/config"
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v, err := strconv.Atoi(os.Getenv(k)); err == nil {
		return v
	}
	return def
}

func driver(host string) neo4j.Driver {
	d, err := neo4j.NewDriver("bolt://"+host, neo4j.NoAuth(), func(c *config.Config) {
		c.SocketConnectTimeout = time.Second
		c.ConnectionAcquisitionTimeout = 2 * time.Second
		c.MaxConnectionPoolSize = 64
	})
	if err != nil {
		panic(err)
	}
	return d
}

// run executes one auto-commit query (Memgraph refuses some statements in explicit transactions).
func run(ctx context.Context, d neo4j.Driver, q string, p map[string]any) ([]*neo4j.Record, error) {
	s := d.NewSession(ctx, neo4j.SessionConfig{})
	defer s.Close(ctx)
	r, err := s.Run(ctx, q, p)
	if err != nil {
		return nil, err
	}
	return r.Collect(ctx)
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("usage: client write|verify|role")
		os.Exit(2)
	}
	hosts := strings.Split(env("HOSTS", "memgraph:7687"), ",")
	switch os.Args[1] {
	case "write":
		write(hosts)
	case "verify":
		if !verify(hosts) {
			os.Exit(1)
		}
	case "role":
		role(hosts)
	default:
		fmt.Println("unknown mode", os.Args[1])
		os.Exit(2)
	}
}

func shortErr(err error) string {
	s := err.Error()
	if i := strings.Index(s, "{message: "); i >= 0 {
		s = s[i+10:]
		if j := strings.Index(s, "}"); j >= 0 {
			s = s[:j]
		}
	}
	if len(s) > 110 {
		s = s[:110] + "..."
	}
	return s
}

func write(hosts []string) {
	ctx := context.Background()
	dur := time.Duration(envInt("DURATION", 45)) * time.Second
	workers := envInt("WORKERS", 4)
	ackedFile := env("ACKED_FILE", "/results/acked.txt")
	drivers := make([]neo4j.Driver, len(hosts))
	for i, h := range hosts {
		drivers[i] = driver(h)
	}
	f, err := os.Create(ackedFile)
	if err != nil {
		panic(err)
	}
	var fmu sync.Mutex
	bw := bufio.NewWriter(f)

	var seq, acked, failed atomic.Int64
	var cur atomic.Int64 // index of the host believed to be MAIN
	var lastAck atomic.Int64
	var maxGap atomic.Int64
	var gapStart atomic.Int64
	errs := map[string]int{}
	var emu sync.Mutex
	start := time.Now()
	lastAck.Store(start.UnixNano())
	deadline := start.Add(dur)

	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for time.Now().Before(deadline) {
				s := seq.Add(1)
				for time.Now().Before(deadline) {
					h := cur.Load()
					_, err := run(ctx, drivers[h], "CREATE (:Tick {seq: $s})", map[string]any{"s": s})
					if err == nil {
						now := time.Now().UnixNano()
						if prev := lastAck.Swap(now); now-prev > maxGap.Load() {
							maxGap.Store(now - prev)
							gapStart.Store(prev)
						}
						acked.Add(1)
						fmu.Lock()
						fmt.Fprintln(bw, s)
						fmu.Unlock()
						break
					}
					failed.Add(1)
					emu.Lock()
					errs[hosts[h]+": "+shortErr(err)]++
					emu.Unlock()
					cur.CompareAndSwap(h, (h+1)%int64(len(hosts))) // try the next host
					time.Sleep(20 * time.Millisecond)
				}
			}
		}()
	}
	// progress: one line per second
	done := make(chan struct{})
	go func() {
		t := time.NewTicker(time.Second)
		defer t.Stop()
		var prev int64
		for i := 1; ; i++ {
			select {
			case <-done:
				return
			case <-t.C:
				a := acked.Load()
				fmu.Lock()
				bw.Flush()
				fmu.Unlock()
				fmt.Printf("t=%ds writes/s=%d failed=%d main=%s\n", i, a-prev, failed.Load(), hosts[cur.Load()])
				prev = a
			}
		}
	}()
	wg.Wait()
	close(done)
	fmu.Lock()
	bw.Flush()
	f.Close()
	fmu.Unlock()
	el := time.Since(start).Seconds()
	fmt.Printf("\nwrite: %d acknowledged in %.1f s (%.0f/s average, %d workers), %d failed attempts (retried)\n",
		acked.Load(), el, float64(acked.Load())/el, workers, failed.Load())
	gs := time.Unix(0, gapStart.Load())
	fmt.Printf("longest gap between two acknowledged writes: %d ms (from t=%.1fs)\n",
		maxGap.Load()/1e6, gs.Sub(start).Seconds())
	keys := make([]string, 0, len(errs))
	for k := range errs {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		fmt.Printf("  %5d x %s\n", errs[k], k)
	}
}

func readAcked(path string) map[int64]bool {
	f, err := os.Open(path)
	if err != nil {
		panic(err)
	}
	defer f.Close()
	m := map[int64]bool{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		v, err := strconv.ParseInt(strings.TrimSpace(sc.Text()), 10, 64)
		if err == nil {
			m[v] = true
		}
	}
	return m
}

func verify(hosts []string) bool {
	ctx := context.Background()
	acked := readAcked(env("ACKED_FILE", "/results/acked.txt"))
	ok := true
	fmt.Printf("verify: %d acknowledged writes in %s\n", len(acked), env("ACKED_FILE", "/results/acked.txt"))
	for _, h := range hosts {
		d := driver(h)
		recs, err := run(ctx, d, "MATCH (t:Tick) RETURN t.seq", nil)
		d.Close(ctx)
		if err != nil {
			fmt.Printf("  %-26s unreachable: %s\n", h, shortErr(err))
			continue
		}
		have := make(map[int64]bool, len(recs))
		for _, r := range recs {
			have[r.Values[0].(int64)] = true
		}
		missing, extra := 0, 0
		for s := range acked {
			if !have[s] {
				missing++
			}
		}
		for s := range have {
			if !acked[s] {
				extra++
			}
		}
		if missing > 0 && os.Getenv("ALLOW_MISSING") == "" {
			ok = false
		}
		fmt.Printf("  %-26s %7d :Tick nodes, acknowledged but missing: %d, present but never acknowledged: %d, duplicate seqs: %d\n",
			h, len(recs), missing, extra, len(recs)-len(have))
	}
	return ok
}

func role(hosts []string) {
	ctx := context.Background()
	for _, h := range hosts {
		d := driver(h)
		r, err := run(ctx, d, "SHOW REPLICATION ROLE", nil)
		if err != nil {
			fmt.Printf("  %-26s %s\n", h, shortErr(err))
			d.Close(ctx)
			continue
		}
		c, err := run(ctx, d, "MATCH (t:Tick) RETURN count(t), max(t.seq)", nil)
		d.Close(ctx)
		if err != nil {
			fmt.Printf("  %-26s role=%v count: %s\n", h, r[0].Values[0], shortErr(err))
			continue
		}
		fmt.Printf("  %-26s role=%-8v ticks=%-7v max_seq=%v\n", h, r[0].Values[0], c[0].Values[0], c[0].Values[1])
	}
}
