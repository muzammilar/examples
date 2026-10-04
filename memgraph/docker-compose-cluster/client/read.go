package main

// client read: read scaling over replicas, used by `make scale-demo`.
//
// Seeds a :Person/:KNOWS graph on MAIN (once), then for DURATION seconds:
//   - READERS goroutines run 2-hop counts, round-robin over the replicas that MAIN's
//     SHOW REPLICAS lists (re-read every 500 ms: client-side read routing, since
//     Community has no routing table without the Enterprise coordinators);
//   - one writer adds WRITE_RATE :KNOWS edges per second on MAIN.
// Prints one line per second and a summary per number of replicas in rotation.

import (
	"context"
	"fmt"
	"math/rand/v2"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/neo4j/neo4j-go-driver/v6/neo4j"
)

type pool struct {
	mu      sync.RWMutex
	hosts   []string
	drivers map[string]neo4j.Driver
}

func (p *pool) set(hosts []string) {
	sort.Strings(hosts)
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, h := range hosts {
		if _, ok := p.drivers[h]; !ok {
			p.drivers[h] = driver(h)
		}
	}
	p.hosts = hosts
}

func (p *pool) pick(i uint64) (string, neo4j.Driver) {
	p.mu.RLock()
	defer p.mu.RUnlock()
	if len(p.hosts) == 0 {
		return "", nil
	}
	h := p.hosts[i%uint64(len(p.hosts))]
	return h, p.drivers[h]
}

func (p *pool) size() int {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return len(p.hosts)
}

// readyReplicas parses SHOW REPLICAS on MAIN: name, socket_address, sync_mode, system_info, data_info.
func readyReplicas(ctx context.Context, d neo4j.Driver) ([]string, error) {
	recs, err := run(ctx, d, "SHOW REPLICAS", nil)
	if err != nil {
		return nil, err
	}
	var out []string
	for _, r := range recs {
		addr, _ := r.Values[1].(string)
		info, _ := r.Values[4].(map[string]any)
		db, _ := info["memgraph"].(map[string]any)
		// every registered replica except "diverged". Under constant writes an ASYNC replica
		// flips between "ready", "replicating", "recovery" and "invalid" while it is only a few
		// commits behind, so filtering on "ready" would leave it out most of the time. Reads
		// from a replica can be stale by that much.
		if st, _ := db["status"].(string); st == "diverged" || st == "" {
			continue
		}
		host := strings.Split(addr, ":")[0]
		out = append(out, host+":7687")
	}
	return out, nil
}

type second struct {
	replicas int
	lat      []float64
	failed   int64
}

func pctl(x []float64, p float64) float64 {
	if len(x) == 0 {
		return 0
	}
	sort.Float64s(x)
	return x[int(p*float64(len(x)-1))]
}

func read() {
	ctx := context.Background()
	mainHost := env("MAIN", "memgraph-main:7687")
	dur := time.Duration(envInt("DURATION", 90)) * time.Second
	readers := envInt("READERS", 16)
	persons := envInt("PERSONS", 20000)
	degree := envInt("DEGREE", 10)
	writeRate := envInt("WRITE_RATE", 200)
	md := driver(mainHost)

	// seed once
	c, err := run(ctx, md, "MATCH (p:Person) RETURN count(p)", nil)
	if err != nil {
		panic(err)
	}
	if c[0].Values[0].(int64) < int64(persons) {
		t := time.Now()
		run(ctx, md, "CREATE INDEX ON :Person(id)", nil)
		if _, err := run(ctx, md, "UNWIND range(0, $n - 1) AS i MERGE (:Person {id: i})", map[string]any{"n": persons}); err != nil {
			panic(err)
		}
		rng := rand.New(rand.NewPCG(42, 1))
		for b := 0; b < persons*degree/10000; b++ {
			rows := make([]any, 10000)
			for i := range rows {
				rows[i] = []any{rng.IntN(persons), rng.IntN(persons)}
			}
			if _, err := run(ctx, md, "UNWIND $rows AS r MATCH (a:Person {id: r[0]}), (b:Person {id: r[1]}) CREATE (a)-[:KNOWS]->(b)",
				map[string]any{"rows": rows}); err != nil {
				panic(err)
			}
		}
		fmt.Printf("seeded %d :Person, %d :KNOWS on MAIN in %.1f s\n", persons, persons*degree, time.Since(t).Seconds())
	}

	p := &pool{drivers: map[string]neo4j.Driver{}}
	if hs, err := readyReplicas(ctx, md); err == nil {
		p.set(hs)
	}
	stop := make(chan struct{})
	go func() { // discovery
		t := time.NewTicker(500 * time.Millisecond)
		defer t.Stop()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
				if hs, err := readyReplicas(ctx, md); err == nil {
					p.set(hs)
				}
			}
		}
	}()

	var writes, writeFail atomic.Int64
	go func() { // writer on MAIN
		rng := rand.New(rand.NewPCG(7, 7))
		t := time.NewTicker(time.Second / time.Duration(max(writeRate, 1)))
		defer t.Stop()
		for {
			select {
			case <-stop:
				return
			case <-t.C:
				_, err := run(ctx, md, "MATCH (a:Person {id: $a}), (b:Person {id: $b}) CREATE (a)-[:KNOWS]->(b)",
					map[string]any{"a": rng.IntN(persons), "b": rng.IntN(persons)})
				if err != nil {
					writeFail.Add(1)
				} else {
					writes.Add(1)
				}
			}
		}
	}()

	var mu sync.Mutex
	cur := &second{}
	var rr atomic.Uint64
	errs := map[string]int{}
	deadline := time.Now().Add(dur)
	var wg sync.WaitGroup
	for w := 0; w < readers; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			rng := rand.New(rand.NewPCG(uint64(w), 3))
			for time.Now().Before(deadline) {
				h, d := p.pick(rr.Add(1))
				if d == nil {
					time.Sleep(50 * time.Millisecond)
					continue
				}
				t := time.Now()
				_, err := run(ctx, d, "MATCH (:Person {id: $id})-[:KNOWS]->()-[:KNOWS]->(c) RETURN count(DISTINCT c)",
					map[string]any{"id": rng.IntN(persons)})
				ms := float64(time.Since(t).Microseconds()) / 1000
				mu.Lock()
				if err != nil {
					cur.failed++
					errs[h+": "+shortErr(err)]++
				} else {
					cur.lat = append(cur.lat, ms)
				}
				mu.Unlock()
				if err != nil {
					time.Sleep(20 * time.Millisecond)
				}
			}
		}(w)
	}

	type phase struct {
		n      int
		secs   int
		reads  int
		failed int64
		lat    []float64
	}
	var phases []*phase
	tick := time.NewTicker(time.Second)
	var prevW int64
	for i := 1; time.Now().Before(deadline); i++ {
		<-tick.C
		mu.Lock()
		s := cur
		cur = &second{}
		mu.Unlock()
		n := p.size()
		w := writes.Load()
		fmt.Printf("t=%3ds replicas=%d reads/s=%6d p50=%6.2fms p99=%6.2fms failed=%d writes/s=%d\n",
			i, n, len(s.lat), pctl(s.lat, 0.5), pctl(s.lat, 0.99), s.failed, w-prevW)
		prevW = w
		if len(phases) == 0 || phases[len(phases)-1].n != n {
			phases = append(phases, &phase{n: n})
		}
		ph := phases[len(phases)-1]
		ph.secs++
		ph.reads += len(s.lat)
		ph.failed += s.failed
		ph.lat = append(ph.lat, s.lat...)
	}
	tick.Stop()
	wg.Wait()
	close(stop)
	fmt.Printf("\nreplicas in rotation | seconds | reads/s | p50 ms | p99 ms | failed reads\n")
	for _, ph := range phases {
		fmt.Printf("%20d | %7d | %7.0f | %6.2f | %6.2f | %d\n", ph.n, ph.secs, float64(ph.reads)/float64(ph.secs),
			pctl(ph.lat, 0.5), pctl(ph.lat, 0.99), ph.failed)
	}
	fmt.Printf("writes on MAIN: %d ok, %d failed\n", writes.Load(), writeFail.Load())
	for k, v := range errs {
		fmt.Printf("  %5d x %s\n", v, k)
	}
	_ = os.Stdout.Sync()
}
