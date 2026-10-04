// Real-time fraud detection on a payment graph, run against Memgraph and Neo4j with the same data
// and the same queries (only DDL and the variable-length filter syntax differ), via Bolt with
// neo4j-go-driver. Phases per engine:
//
//  1. load      accounts, devices, USES and TRANSFER edges (UNWIND batches)
//  2. stream    STREAM transfers from WORKERS goroutines; each one is a payment authorization:
//               a risk check (shared device with a flagged account, flagged accounts within
//               1..3 transfer hops downstream of the receiver), then the insert of the transfer
//  3. analytics ring detection (cycles of 3..5 large transfers), shared-device clusters,
//               exposure of every flagged account (accounts that reach it in 1..3 hops),
//               top receivers; each run RUNS times
//  4. check     analytics rows hashed per engine; planted rings must all be found; edge counts
//
// Exits non-zero if an engine misses a planted ring, if counts are off, or if the engines'
// result rows differ.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"math/rand/v2"
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

var (
	accounts  = envInt("ACCOUNTS", 50000)
	devices   = envInt("DEVICES", 20000)
	transfers = envInt("TRANSFERS", 300000)
	stream    = envInt("STREAM", 20000)
	workers   = envInt("WORKERS", 8)
	runs      = envInt("RUNS", 5)
	flaggedN  = envInt("FLAGGED", 200)
	ringsN    = envInt("RINGS", 40)       // planted in the initial load
	ringsLive = envInt("STREAM_RINGS", 10) // closed by the stream
	seed      = uint64(envInt("SEED", 42))
)

const bigAmount = 5000 // ring transfers are >= this; normal transfers rarely are

// ---------------------------------------------------------------- data

type transfer struct {
	ID       int
	Src, Dst int
	Amount   int
	TS       int
}

type dataset struct {
	flagged   []bool
	uses      [][2]int // account, device
	initial   []transfer
	streamed  []transfer
	rings     [][]int // planted rings, as account ids in order
	liveRings [][]int
}

func generate() *dataset {
	rng := rand.New(rand.NewPCG(seed, 1))
	d := &dataset{flagged: make([]bool, accounts)}
	for _, i := range rng.Perm(accounts)[:flaggedN] {
		d.flagged[i] = true
	}
	for a := 0; a < accounts; a++ {
		d.uses = append(d.uses, [2]int{a, rng.IntN(devices)})
		if rng.IntN(10) == 0 { // 10% use a second device
			d.uses = append(d.uses, [2]int{a, rng.IntN(devices)})
		}
	}
	id := 0
	amount := func() int {
		if rng.IntN(200) == 0 { // 0.5% large
			return bigAmount + rng.IntN(20000)
		}
		return 10 + rng.IntN(2000)
	}
	// skewed receivers: a few merchants-like accounts receive a lot
	recv := func() int { return int(float64(accounts) * pow(rng.Float64(), 2)) }
	for i := 0; i < transfers; i++ {
		s, r := rng.IntN(accounts), recv()
		if s == r {
			r = (r + 1) % accounts
		}
		d.initial = append(d.initial, transfer{id, s, r, amount(), i})
		id++
	}
	ring := func() []int {
		n := 3 + rng.IntN(3)
		members := rng.Perm(accounts)[:n]
		return members
	}
	for k := 0; k < ringsN; k++ {
		m := ring()
		d.rings = append(d.rings, m)
		for j := range m {
			d.initial = append(d.initial, transfer{id, m[j], m[(j+1)%len(m)], bigAmount + rng.IntN(20000), transfers + id})
			id++
		}
	}
	// stream: normal transfers, plus rings whose last edge arrives in the stream
	var live []transfer
	for k := 0; k < ringsLive; k++ {
		m := ring()
		d.liveRings = append(d.liveRings, m)
		for j := range m {
			t := transfer{id, m[j], m[(j+1)%len(m)], bigAmount + rng.IntN(20000), 0}
			id++
			if j < len(m)-1 {
				d.initial = append(d.initial, t)
			} else {
				live = append(live, t)
			}
		}
	}
	for i := 0; i < stream-len(live); i++ {
		s, r := rng.IntN(accounts), recv()
		if s == r {
			r = (r + 1) % accounts
		}
		d.streamed = append(d.streamed, transfer{id, s, r, amount(), 0})
		id++
	}
	// spread the ring-closing transfers through the stream
	for i, t := range live {
		pos := (i + 1) * len(d.streamed) / (len(live) + 1)
		d.streamed = append(d.streamed[:pos], append([]transfer{t}, d.streamed[pos:]...)...)
	}
	for i := range d.streamed {
		d.streamed[i].TS = 10_000_000 + i
	}
	return d
}

func pow(x float64, n int) float64 {
	r := 1.0
	for i := 0; i < n; i++ {
		r *= x
	}
	return r
}

// ---------------------------------------------------------------- engines

type engine struct {
	name     string
	uri      string
	auth     neo4j.AuthToken
	ddl      []string
	ringExpr string // variable-length pattern for ring detection, engine syntax
	drv      neo4j.Driver
}

func engines() []*engine {
	all := map[string]*engine{
		"memgraph": {
			name: "memgraph",
			uri:  env("MEMGRAPH_URI", "bolt://memgraph:7687"),
			auth: neo4j.NoAuth(),
			ddl: []string{
				"CREATE INDEX ON :Account(id)",
				"CREATE INDEX ON :Device(id)",
				"CREATE INDEX ON :Account(flagged)",
				"CREATE CONSTRAINT ON (a:Account) ASSERT a.id IS UNIQUE",
				"CREATE CONSTRAINT ON (d:Device) ASSERT d.id IS UNIQUE",
			},
			// filter lambda: the amount predicate is applied during the expansion
			ringExpr: "[:TRANSFER *3..5 (e, n | e.amount >= 5000)]",
		},
		"neo4j": {
			name: "neo4j",
			uri:  env("NEO4J_URI", "bolt://neo4j:7687"),
			auth: neo4j.BasicAuth("neo4j", env("NEO4J_PASSWORD", "demo-password"), ""),
			ddl: []string{
				"CREATE CONSTRAINT account_id IF NOT EXISTS FOR (a:Account) REQUIRE a.id IS UNIQUE",
				"CREATE CONSTRAINT device_id IF NOT EXISTS FOR (d:Device) REQUIRE d.id IS UNIQUE",
				"CREATE INDEX account_flagged IF NOT EXISTS FOR (a:Account) ON (a.flagged)",
				"CALL db.awaitIndexes(300)",
			},
			// filtered with all(...) over the relationship list in ringQuery
			ringExpr: "[r:TRANSFER*3..5]",
		},
	}
	var out []*engine
	for _, n := range strings.Split(env("ENGINES", "memgraph,neo4j"), ",") {
		e, ok := all[strings.TrimSpace(n)]
		if !ok {
			fmt.Println("unknown engine", n)
			os.Exit(2)
		}
		out = append(out, e)
	}
	return out
}

func (e *engine) connect() {
	d, err := neo4j.NewDriver(e.uri, e.auth, func(c *config.Config) { c.MaxConnectionPoolSize = 64 })
	if err != nil {
		panic(err)
	}
	ctx := context.Background()
	for i := 0; ; i++ {
		if err = d.VerifyConnectivity(ctx); err == nil {
			break
		}
		if i > 60 {
			panic(err)
		}
		time.Sleep(time.Second)
	}
	e.drv = d
}

// q runs one auto-commit query (Memgraph refuses DDL in explicit transactions).
func (e *engine) q(query string, p map[string]any) ([]*neo4j.Record, error) {
	ctx := context.Background()
	s := e.drv.NewSession(ctx, neo4j.SessionConfig{})
	defer s.Close(ctx)
	r, err := s.Run(ctx, query, p)
	if err != nil {
		return nil, err
	}
	return r.Collect(ctx)
}

func (e *engine) must(query string, p map[string]any) []*neo4j.Record {
	r, err := e.q(query, p)
	if err != nil {
		fmt.Printf("%s: %v\nquery: %s\n", e.name, err, query)
		os.Exit(1)
	}
	return r
}

func (e *engine) ringQuery() string {
	where := "WHERE all(n IN nodes(p) WHERE n.id >= a.id)"
	if e.name == "neo4j" {
		where = "WHERE all(x IN r WHERE x.amount >= 5000) AND all(n IN nodes(p) WHERE n.id >= a.id)"
	}
	return "MATCH p = (a:Account)-" + e.ringExpr + "->(a) " + where +
		" RETURN [n IN nodes(p) | n.id] AS ring ORDER BY ring"
}

// analytics: name -> query; identical text on both engines except the ring query
func (e *engine) analytics() [][2]string {
	return [][2]string{
		{"rings", e.ringQuery()},
		{"shared devices", "MATCH (d:Device)<-[:USES]-(a:Account) " +
			"WITH d, count(a) AS accts, sum(CASE WHEN a.flagged THEN 1 ELSE 0 END) AS flagged " +
			"WHERE accts >= 5 AND flagged > 0 RETURN d.id AS device, accts, flagged ORDER BY device"},
		{"exposure", "MATCH (f:Account {flagged: true})<-[:TRANSFER*1..3]-(a:Account) " +
			"RETURN f.id AS flagged, count(DISTINCT a) AS reachable_from ORDER BY flagged"},
		{"top receivers", "MATCH (:Account)-[t:TRANSFER]->(r:Account) " +
			"RETURN r.id AS account, count(t) AS n, sum(t.amount) AS total ORDER BY total DESC, account LIMIT 20"},
	}
}

const riskQuery = "MATCH (d:Account {id: $dst}) " +
	"OPTIONAL MATCH (d)-[:USES]->(:Device)<-[:USES]-(f:Account {flagged: true}) " +
	"WITH d, count(DISTINCT f) AS shared_device " +
	"OPTIONAL MATCH (d)-[:TRANSFER*1..3]->(g:Account {flagged: true}) " +
	"RETURN shared_device, count(DISTINCT g) AS flagged_downstream"

const insertQuery = "MATCH (s:Account {id: $src}), (d:Account {id: $dst}) " +
	"CREATE (s)-[:TRANSFER {id: $id, amount: $amount, ts: $ts, risk: $risk}]->(d)"

// ---------------------------------------------------------------- measurement

type stats struct{ lat []float64 }

func (s *stats) add(d time.Duration) { s.lat = append(s.lat, float64(d.Microseconds())/1000) }
func (s *stats) p(q float64) float64 {
	if len(s.lat) == 0 {
		return 0
	}
	x := append([]float64(nil), s.lat...)
	sort.Float64s(x)
	return x[int(q*float64(len(x)-1))]
}

type result struct {
	engine, version string
	rows            map[string]string // phase -> printed row
	hashes          map[string]string
	counts          map[string]int
	ok              bool
}

func row(name string, n int, sec float64, s *stats, extra string) string {
	return fmt.Sprintf("%-26s %8d %9.1f %10.0f %8.2f %8.2f  %s", name, n, sec, float64(n)/sec, s.p(0.5), s.p(0.99), extra)
}

func batches[T any](xs []T, n int, f func([]T)) {
	for i := 0; i < len(xs); i += n {
		f(xs[i:min(i+n, len(xs))])
	}
}

func runEngine(e *engine, d *dataset) *result {
	res := &result{engine: e.name, rows: map[string]string{}, hashes: map[string]string{}, counts: map[string]int{}, ok: true}
	e.connect()
	defer e.drv.Close(context.Background())
	if e.name == "memgraph" {
		res.version = fmt.Sprint(e.must("SHOW VERSION", nil)[0].Values[0])
	} else {
		r := e.must("CALL dbms.components() YIELD versions, edition RETURN versions[0], edition", nil)[0]
		res.version = fmt.Sprintf("%v %v", r.Values[0], r.Values[1])
	}
	fmt.Printf("\n==> %s %s\n", e.name, res.version)
	e.must("MATCH (n) DETACH DELETE n", nil) // fresh state (small enough for one transaction)
	for _, q := range e.ddl {
		e.must(q, nil)
	}

	// 1. load
	t0 := time.Now()
	var ls stats
	acc := make([]any, accounts)
	for i := range acc {
		acc[i] = map[string]any{"id": i, "flagged": d.flagged[i]}
	}
	batches(acc, 10000, func(b []any) {
		t := time.Now()
		e.must("UNWIND $rows AS r CREATE (:Account {id: r.id, flagged: r.flagged})", map[string]any{"rows": b})
		ls.add(time.Since(t))
	})
	dev := make([]any, devices)
	for i := range dev {
		dev[i] = i
	}
	batches(dev, 10000, func(b []any) {
		t := time.Now()
		e.must("UNWIND $rows AS i CREATE (:Device {id: i})", map[string]any{"rows": b})
		ls.add(time.Since(t))
	})
	uses := make([]any, len(d.uses))
	for i, u := range d.uses {
		uses[i] = []any{u[0], u[1]}
	}
	batches(uses, 10000, func(b []any) {
		t := time.Now()
		e.must("UNWIND $rows AS r MATCH (a:Account {id: r[0]}), (d:Device {id: r[1]}) CREATE (a)-[:USES]->(d)", map[string]any{"rows": b})
		ls.add(time.Since(t))
	})
	tr := make([]any, len(d.initial))
	for i, t := range d.initial {
		tr[i] = []any{t.ID, t.Src, t.Dst, t.Amount, t.TS}
	}
	batches(tr, 10000, func(b []any) {
		t := time.Now()
		e.must("UNWIND $rows AS r MATCH (s:Account {id: r[1]}), (d:Account {id: r[2]}) "+
			"CREATE (s)-[:TRANSFER {id: r[0], amount: r[3], ts: r[4]}]->(d)", map[string]any{"rows": b})
		ls.add(time.Since(t))
	})
	n := accounts + devices + len(d.uses) + len(d.initial)
	res.rows["1 load"] = row("1 load (rows, batch 10k)", n, time.Since(t0).Seconds(), &ls, "per batch")
	fmt.Println(res.rows["1 load"])

	// 2. stream
	var check, insert stats
	var mu sync.Mutex
	var next atomic.Int64
	var flaggedHits, errs atomic.Int64
	t0 = time.Now()
	var wg sync.WaitGroup
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			var c, in stats
			for {
				i := int(next.Add(1)) - 1
				if i >= len(d.streamed) {
					break
				}
				t := d.streamed[i]
				s := time.Now()
				r, err := e.q(riskQuery, map[string]any{"dst": t.Dst})
				c.add(time.Since(s))
				risk := int64(0)
				if err != nil {
					errs.Add(1)
				} else if len(r) > 0 {
					risk = r[0].Values[0].(int64) + r[0].Values[1].(int64)
				}
				if risk > 0 {
					flaggedHits.Add(1)
				}
				s = time.Now()
				if _, err := e.q(insertQuery, map[string]any{"src": t.Src, "dst": t.Dst, "id": t.ID,
					"amount": t.Amount, "ts": t.TS, "risk": risk}); err != nil {
					errs.Add(1)
					fmt.Println("insert:", err)
				}
				in.add(time.Since(s))
			}
			mu.Lock()
			check.lat = append(check.lat, c.lat...)
			insert.lat = append(insert.lat, in.lat...)
			mu.Unlock()
		}()
	}
	wg.Wait()
	sec := time.Since(t0).Seconds()
	res.rows["2 stream"] = row(fmt.Sprintf("2 stream (%d workers)", workers), len(d.streamed), sec, &check,
		fmt.Sprintf("risk check; %d transfers had risk > 0, %d errors", flaggedHits.Load(), errs.Load()))
	res.rows["2 stream insert"] = row("  insert", len(d.streamed), sec, &insert, "")
	fmt.Println(res.rows["2 stream"])
	fmt.Println(res.rows["2 stream insert"])
	if errs.Load() > 0 {
		res.ok = false
	}

	// 3. analytics
	for i, a := range e.analytics() {
		var s stats
		var recs []*neo4j.Record
		for k := 0; k < runs; k++ {
			t := time.Now()
			recs = e.must(a[1], nil)
			s.add(time.Since(t))
		}
		rows := make([]string, len(recs))
		for j, r := range recs {
			b, _ := json.Marshal(r.Values)
			rows[j] = string(b)
		}
		h := sha256.Sum256([]byte(strings.Join(rows, "\n")))
		res.hashes[a[0]] = fmt.Sprintf("%x", h[:6])
		res.counts[a[0]] = len(rows)
		key := fmt.Sprintf("3.%d %s", i+1, a[0])
		res.rows[key] = fmt.Sprintf("%-26s %8d runs, p50 %9.2f ms, p99 %9.2f ms  %5d rows  sha %s", "3 "+a[0], runs,
			s.p(0.5), s.p(0.99), len(rows), res.hashes[a[0]])
		fmt.Println(res.rows[key])
		if a[0] == "rings" {
			found := map[string]bool{}
			for _, r := range recs {
				found[canon(r.Values[0].([]any))] = true
			}
			miss := 0
			for _, m := range append(append([][]int{}, d.rings...), d.liveRings...) {
				if !found[canonInts(m)] {
					miss++
				}
			}
			res.rows["4 rings"] = fmt.Sprintf("%-26s %d planted (%d closed by the stream), %d found, %d missed", "4 check rings",
				len(d.rings)+len(d.liveRings), len(d.liveRings), len(recs), miss)
			fmt.Println(res.rows["4 rings"])
			if miss > 0 {
				res.ok = false
			}
		}
	}

	// 4. counts
	c := e.must("MATCH ()-[t:TRANSFER]->() RETURN count(t), sum(t.amount)", nil)[0]
	want := len(d.initial) + len(d.streamed)
	res.rows["4 counts"] = fmt.Sprintf("%-26s %v transfers (want %d), amount sum %v", "4 check counts", c.Values[0], want, c.Values[1])
	fmt.Println(res.rows["4 counts"])
	if c.Values[0].(int64) != int64(want) {
		res.ok = false
	}
	res.hashes["sum"] = fmt.Sprint(c.Values[1])
	return res
}

// canon rotates a ring so its smallest id is first (the queries return rings that way).
func canon(v []any) string {
	ids := make([]int, 0, len(v))
	for _, x := range v[:len(v)-1] { // the path ends where it starts
		ids = append(ids, int(x.(int64)))
	}
	return canonInts(ids)
}

func canonInts(ids []int) string {
	m := 0
	for i := range ids {
		if ids[i] < ids[m] {
			m = i
		}
	}
	r := append(append([]int{}, ids[m:]...), ids[:m]...)
	return fmt.Sprint(r)
}

func main() {
	t := time.Now()
	d := generate()
	fmt.Printf("generated: %d accounts (%d flagged), %d devices, %d USES, %d initial transfers (%d planted rings), %d streamed (%d ring-closing), seed %d, in %.1fs\n",
		accounts, flaggedN, devices, len(d.uses), len(d.initial), len(d.rings), len(d.streamed), len(d.liveRings), seed, time.Since(t).Seconds())
	fmt.Printf("%-26s %8s %9s %10s %8s %8s\n", "phase", "n", "seconds", "per sec", "p50 ms", "p99 ms")
	var results []*result
	for _, e := range engines() {
		results = append(results, runEngine(e, d))
	}
	ok := true
	for _, r := range results {
		if !r.ok {
			fmt.Printf("FAIL: %s did not pass its own checks\n", r.engine)
			ok = false
		}
	}
	if len(results) > 1 {
		fmt.Println("\n==> same result rows on every engine?")
		names := []string{"rings", "shared devices", "exposure", "top receivers", "sum"}
		for _, n := range names {
			same := true
			for _, r := range results[1:] {
				if r.hashes[n] != results[0].hashes[n] {
					same = false
				}
			}
			parts := []string{}
			for _, r := range results {
				parts = append(parts, fmt.Sprintf("%s=%s", r.engine, r.hashes[n]))
			}
			fmt.Printf("  %-15s %-5v %s\n", n, same, strings.Join(parts, " "))
			if !same {
				ok = false
			}
		}
	}
	if !ok {
		fmt.Println("\nCHECK FAILED")
		os.Exit(1)
	}
	fmt.Println("\nALL CHECKS PASSED")
}
