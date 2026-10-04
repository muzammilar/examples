// Log search on Manticore Search vs Elasticsearch, same generated data, same queries.
//
//  1. generate DOCS web/app log lines deterministically from their id (both engines get
//     identical documents)
//  2. ingest into Manticore (multi-row INSERT over the MySQL protocol) and into Elasticsearch
//     (_bulk, official go-elasticsearch client), WRITERS concurrent writers, BATCH docs per
//     request, one engine after the other
//  3. size on disk and in memory as each engine reports it
//  4. queries (full-text, phrase, full-text + filters, aggregations): first a fixed variant on
//     both engines whose hit counts / buckets must match exactly, then RUNS randomized
//     variants per query from CLIENTS concurrent clients for p50 / p99 / queries per second
//
// Exits non-zero if any count differs between the engines or from the generator's own count.
package main

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"math"
	"math/rand"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/elastic/go-elasticsearch/v9"
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
		fail("bad %s: %v", k, err)
	}
	return v
}

func fail(f string, a ...any) {
	fmt.Fprintf(os.Stderr, "FATAL: "+f+"\n", a...)
	os.Exit(1)
}

// ---------------------------------------------------------------------------- data

var (
	services = []string{"api", "auth", "billing", "cart", "catalog", "checkout", "search", "gateway", "worker", "notifier"}
	methods  = []string{"GET", "GET", "GET", "GET", "POST", "POST", "PUT", "DELETE"}
	paths    = []string{"/api/v1/orders", "/api/v1/cart", "/api/v1/users", "/api/v1/products", "/api/v1/search", "/api/v1/payments", "/login", "/logout", "/health", "/api/v1/checkout"}
	levels   = []string{"info", "info", "info", "info", "info", "info", "warn", "warn", "error"}
	// message templates; %d = a number, %s = a word from extra
	okMsgs = []string{
		"request completed for user %d in region %s",
		"order %d created with %d items",
		"cache hit for product %d",
		"cache miss for product %d loading from database",
		"user %d logged in from %s",
		"user %d logged out",
		"search query returned %d results for category %s",
		"payment %d authorized by provider %s",
		"session %d refreshed",
		"health check passed",
		"checkout started for cart %d with %d items",
		"inventory reserved for order %d",
	}
	warnMsgs = []string{
		"slow query on table %s took %d ms",
		"retrying request to %s attempt %d",
		"rate limit approaching for client %d",
		"cache eviction pressure on shard %d",
		"payment provider %s responded slowly after %d ms",
		"deprecated api version used by client %d",
	}
	errMsgs = []string{
		"upstream timed out while reading response header from %s",
		"connection refused by upstream %s on port %d",
		"connection reset by peer while sending to %s",
		"database deadlock detected on table %s retry %d",
		"payment declined by provider %s for order %d",
		"timeout waiting for lock on order %d",
		"out of memory while processing batch %d",
		"certificate expired for host %s",
		"checkout failed for cart %d: inventory unavailable",
		"null pointer exception in handler %s",
	}
	extra = []string{"eu", "us", "asia", "stripe", "adyen", "paypal", "orders", "users", "carts", "inventory", "redis", "postgres", "kafka", "backend", "frontend", "mobile", "web", "desktop"}
)

const t0 = 1790000000 // 2026-09-21T14:13:20Z: logs span DAYS days from here

type doc struct {
	ID        int64
	TS        int64
	Service   string
	Method    string
	Path      string
	Status    int
	Bytes     int
	LatencyMS int
	Level     string
	ClientIP  string
	Message   string
}

func fill(t string, r *rand.Rand) string {
	var b strings.Builder
	for i := 0; i < len(t); i++ {
		if t[i] == '%' && i+1 < len(t) {
			switch t[i+1] {
			case 'd':
				b.WriteString(strconv.Itoa(r.Intn(100000)))
			case 's':
				b.WriteString(extra[r.Intn(len(extra))])
			}
			i++
			continue
		}
		b.WriteByte(t[i])
	}
	return b.String()
}

var spanSecs int64

// splitmix64: a tiny per-document random source (rand.NewSource allocates ~5 KB of state per call)
type splitmix uint64

func (s *splitmix) Uint64() uint64 {
	*s += 0x9e3779b97f4a7c15
	z := uint64(*s)
	z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9
	z = (z ^ (z >> 27)) * 0x94d049bb133111eb
	return z ^ (z >> 31)
}
func (s *splitmix) Int63() int64    { return int64(s.Uint64() >> 1) }
func (s *splitmix) Seed(seed int64) { *s = splitmix(seed) }

func gen(id int64) doc {
	src := splitmix(id)
	r := rand.New(&src)
	d := doc{ID: id, TS: t0 + id*spanSecs/int64(total) + int64(r.Intn(5))}
	d.Service = services[r.Intn(len(services))]
	d.Method = methods[r.Intn(len(methods))]
	d.Path = paths[r.Intn(len(paths))]
	d.Level = levels[r.Intn(len(levels))]
	switch d.Level {
	case "info":
		d.Status = []int{200, 200, 200, 201, 204, 301, 304, 404}[r.Intn(8)]
		d.Message = fill(okMsgs[r.Intn(len(okMsgs))], r)
		d.LatencyMS = 1 + int(r.ExpFloat64()*40)
	case "warn":
		d.Status = []int{200, 429, 404, 400}[r.Intn(4)]
		d.Message = fill(warnMsgs[r.Intn(len(warnMsgs))], r)
		d.LatencyMS = 100 + int(r.ExpFloat64()*400)
	default:
		d.Status = []int{500, 502, 503, 504}[r.Intn(4)]
		d.Message = fill(errMsgs[r.Intn(len(errMsgs))], r)
		d.LatencyMS = 200 + int(r.ExpFloat64()*3000)
	}
	d.Bytes = 100 + r.Intn(50000)
	d.ClientIP = fmt.Sprintf("10.%d.%d.%d", r.Intn(256), r.Intn(256), r.Intn(256))
	d.Message = fmt.Sprintf("%s %s %d %s", d.Method, d.Path, d.Status, d.Message)
	return d
}

var total int

// ---------------------------------------------------------------------------- engines

type engine interface {
	name() string
	setup() error
	ingest(first, n int64) error
	finish() error // make everything searchable and settled
	size() (disk, mem string)
	query(q query, v variant) (result, error)
}

type result struct {
	hits    int64
	buckets map[string]int64 // aggregation buckets (key -> doc count)
}

// ---- Manticore over the MySQL protocol

type manticore struct{ db *sql.DB }

func (m *manticore) name() string { return "manticore" }

func (m *manticore) setup() error {
	for _, s := range []string{
		"DROP TABLE IF EXISTS logs",
		// columnar attribute storage (MCL), secondary indexes built by default
		"CREATE TABLE logs (message text, service string, method string, path string, status int, bytes int, latency_ms int, level string, client_ip string, ts timestamp) engine='columnar'",
	} {
		if _, err := m.db.Exec(s); err != nil {
			return fmt.Errorf("%s: %w", s, err)
		}
	}
	return nil
}

func (m *manticore) ingest(first, n int64) error {
	var b strings.Builder
	b.WriteString("INSERT INTO logs (id, message, service, method, path, status, bytes, latency_ms, level, client_ip, ts) VALUES ")
	for i := int64(0); i < n; i++ {
		d := gen(first + i)
		if i > 0 {
			b.WriteByte(',')
		}
		fmt.Fprintf(&b, "(%d,'%s','%s','%s','%s',%d,%d,%d,'%s','%s',%d)", d.ID, d.Message, d.Service, d.Method, d.Path, d.Status, d.Bytes, d.LatencyMS, d.Level, d.ClientIP, d.TS)
	}
	_, err := m.db.Exec(b.String())
	return err
}

func (m *manticore) finish() error {
	// RT tables are searchable on INSERT; flush the RAM chunk to disk and wait for the background
	// merge of disk chunks so the size and queries reflect the settled table
	if _, err := m.db.Exec("FLUSH RAMCHUNK logs"); err != nil {
		return err
	}
	for i := 0; i < 1800; i++ {
		var k, v string
		if err := m.db.QueryRow("SHOW TABLE logs STATUS LIKE 'optimizing'").Scan(&k, &v); err != nil {
			return err
		}
		if v == "0" {
			return nil
		}
		time.Sleep(time.Second)
	}
	return nil
}

func (m *manticore) size() (string, string) {
	rows, err := m.db.Query("SHOW TABLE logs STATUS")
	if err != nil {
		return err.Error(), ""
	}
	defer rows.Close()
	st := map[string]string{}
	for rows.Next() {
		var k, v string
		rows.Scan(&k, &v)
		st[k] = v
	}
	return fmt.Sprintf("disk_bytes %s (%s disk chunks)", st["disk_bytes"], st["disk_chunks"]), "ram_bytes " + st["ram_bytes"]
}

func (m *manticore) query(q query, v variant) (result, error) {
	s := q.sql(v)
	rows, err := m.db.Query(s)
	if err != nil {
		return result{}, fmt.Errorf("%s: %w", s, err)
	}
	defer rows.Close()
	res := result{buckets: map[string]int64{}}
	for rows.Next() {
		if q.agg {
			var k string
			var c int64
			if err := rows.Scan(&k, &c); err != nil {
				return res, err
			}
			res.buckets[k] = c
			res.hits += c
		} else {
			var id int64
			rows.Scan(&id)
		}
	}
	if err := rows.Err(); err != nil {
		return res, err
	}
	rows.Close()
	if !q.agg {
		// total_found = all matching documents (exact: total_relation eq), not just the LIMIT
		var k, val string
		r2, err := m.db.Query("SHOW META LIKE 'total_found'")
		if err != nil {
			return res, err
		}
		for r2.Next() {
			r2.Scan(&k, &val)
		}
		r2.Close()
		res.hits, _ = strconv.ParseInt(val, 10, 64)
	}
	return res, nil
}

// ---- Elasticsearch over HTTP (official client)

type elastic struct{ es *elasticsearch.Client }

func (e *elastic) name() string { return "elasticsearch" }

func (e *elastic) do(method, path string, body []byte) ([]byte, error) {
	req, _ := http.NewRequest(method, path, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := e.es.Perform(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 && !(method == "DELETE" && resp.StatusCode == 404) {
		return b, fmt.Errorf("%s %s: HTTP %d: %.300s", method, path, resp.StatusCode, b)
	}
	return b, nil
}

func (e *elastic) setup() error {
	e.do("DELETE", "/logs", nil)
	// one primary shard (same as one Manticore table), no replicas (single node); text field with
	// the standard analyzer; keyword fields for exact filters and terms aggregations
	_, err := e.do("PUT", "/logs", []byte(`{
	  "settings": {"number_of_shards": 1, "number_of_replicas": 0},
	  "mappings": {"properties": {
	    "message": {"type": "text"}, "service": {"type": "keyword"}, "method": {"type": "keyword"},
	    "path": {"type": "keyword"}, "status": {"type": "integer"}, "bytes": {"type": "integer"},
	    "latency_ms": {"type": "integer"}, "level": {"type": "keyword"}, "client_ip": {"type": "ip"},
	    "ts": {"type": "date", "format": "epoch_second"}}}}`))
	return err
}

func (e *elastic) ingest(first, n int64) error {
	var b bytes.Buffer
	for i := int64(0); i < n; i++ {
		d := gen(first + i)
		fmt.Fprintf(&b, `{"index":{"_id":"%d"}}`+"\n", d.ID)
		fmt.Fprintf(&b, `{"message":%q,"service":%q,"method":%q,"path":%q,"status":%d,"bytes":%d,"latency_ms":%d,"level":%q,"client_ip":%q,"ts":%d}`+"\n",
			d.Message, d.Service, d.Method, d.Path, d.Status, d.Bytes, d.LatencyMS, d.Level, d.ClientIP, d.TS)
	}
	out, err := e.do("POST", "/logs/_bulk", b.Bytes())
	if err != nil {
		return err
	}
	var r struct {
		Errors bool `json:"errors"`
	}
	json.Unmarshal(out, &r)
	if r.Errors {
		return fmt.Errorf("bulk had item errors: %.500s", out)
	}
	return nil
}

func (e *elastic) finish() error {
	// make all documents searchable (default refresh_interval 1s) and write segments to disk
	if _, err := e.do("POST", "/logs/_refresh", nil); err != nil {
		return err
	}
	_, err := e.do("POST", "/logs/_flush", nil)
	return err
}

func (e *elastic) size() (string, string) {
	out, err := e.do("GET", "/logs/_stats/store,segments", nil)
	if err != nil {
		return err.Error(), ""
	}
	var s struct {
		All struct {
			Primaries struct {
				Store struct {
					Size int64 `json:"size_in_bytes"`
				} `json:"store"`
				Segments struct {
					Count int64 `json:"count"`
				} `json:"segments"`
			} `json:"primaries"`
		} `json:"_all"`
	}
	json.Unmarshal(out, &s)
	out2, _ := e.do("GET", "/_nodes/stats/jvm", nil)
	var n struct {
		Nodes map[string]struct {
			JVM struct {
				Mem struct {
					HeapUsed int64 `json:"heap_used_in_bytes"`
					HeapMax  int64 `json:"heap_max_in_bytes"`
				} `json:"mem"`
			} `json:"jvm"`
		} `json:"nodes"`
	}
	json.Unmarshal(out2, &n)
	var used, max int64
	for _, v := range n.Nodes {
		used, max = v.JVM.Mem.HeapUsed, v.JVM.Mem.HeapMax
	}
	return fmt.Sprintf("store size_in_bytes %d (%d segments)", s.All.Primaries.Store.Size, s.All.Primaries.Segments.Count),
		fmt.Sprintf("JVM heap used %d of %d", used, max)
}

func (e *elastic) query(q query, v variant) (result, error) {
	body := q.es(v)
	// request_cache=false: repeated identical aggregations would otherwise be served from the
	// shard request cache (Manticore's query cache only keeps queries slower than 3 s)
	out, err := e.do("POST", "/logs/_search?request_cache=false", []byte(body))
	if err != nil {
		return result{}, err
	}
	var r struct {
		Hits struct {
			Total struct {
				Value    int64  `json:"value"`
				Relation string `json:"relation"`
			} `json:"total"`
		} `json:"hits"`
		Aggregations struct {
			By struct {
				Buckets []struct {
					Key   any   `json:"key"`
					Count int64 `json:"doc_count"`
				} `json:"buckets"`
			} `json:"by"`
		} `json:"aggregations"`
	}
	if err := json.Unmarshal(out, &r); err != nil {
		return result{}, err
	}
	res := result{hits: r.Hits.Total.Value, buckets: map[string]int64{}}
	if q.agg {
		res.hits = 0
		for _, b := range r.Aggregations.By.Buckets {
			k := fmt.Sprint(b.Key)
			if f, ok := b.Key.(float64); ok {
				k = strconv.FormatInt(int64(f), 10)
			}
			res.buckets[k] = b.Count
			res.hits += b.Count
		}
	}
	return res, nil
}

// ---------------------------------------------------------------------------- queries

type variant struct {
	word              string
	phraseWord, word2 string // the two words of a phrase
	from, to          int64
	status            int
	service           string
}

type query struct {
	label string
	agg   bool
	sql   func(v variant) string
	es    func(v variant) string
	check func(d doc, v variant) (bool, string) // generator-side truth for the fixed variant
}

var words = []string{"timeout", "refused", "deadlock", "declined", "cache", "payment", "checkout", "inventory", "session", "certificate", "memory", "upstream", "retrying", "slow", "logged", "authorized"}

func hasWord(msg, w string) bool {
	for _, t := range strings.FieldsFunc(strings.ToLower(msg), func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9')
	}) {
		if t == w {
			return true
		}
	}
	return false
}

func hasPhrase(msg, a, b string) bool {
	t := strings.FieldsFunc(strings.ToLower(msg), func(r rune) bool { return !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9') })
	for i := 0; i+1 < len(t); i++ {
		if t[i] == a && t[i+1] == b {
			return true
		}
	}
	return false
}

var queries = []query{
	{
		label: "full-text: one word, top 20 by relevance",
		sql:   func(v variant) string { return fmt.Sprintf("SELECT id FROM logs WHERE MATCH('%s') LIMIT 20", v.word) },
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":20,"_source":false,"track_total_hits":true,"query":{"match":{"message":%q}}}`, v.word)
		},
		check: func(d doc, v variant) (bool, string) { return hasWord(d.Message, v.word), "" },
	},
	{
		label: "phrase (\"connection refused\"), top 20",
		sql: func(v variant) string {
			return fmt.Sprintf(`SELECT id FROM logs WHERE MATCH('"%s %s"') LIMIT 20`, v.phraseWord, v.word2)
		},
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":20,"_source":false,"track_total_hits":true,"query":{"match_phrase":{"message":"%s %s"}}}`, v.phraseWord, v.word2)
		},
		check: func(d doc, v variant) (bool, string) { return hasPhrase(d.Message, v.phraseWord, v.word2), "" },
	},
	{
		label: "full-text + status>=500 + 1 h time range, newest 20",
		sql: func(v variant) string {
			return fmt.Sprintf("SELECT id FROM logs WHERE MATCH('%s') AND status >= 500 AND ts >= %d AND ts < %d ORDER BY ts DESC LIMIT 20", v.word, v.from, v.to)
		},
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":20,"_source":false,"track_total_hits":true,"sort":[{"ts":"desc"}],"query":{"bool":{"must":[{"match":{"message":%q}}],"filter":[{"range":{"status":{"gte":500}}},{"range":{"ts":{"gte":%d,"lt":%d,"format":"epoch_second"}}}]}}}`, v.word, v.from, v.to)
		},
		check: func(d doc, v variant) (bool, string) {
			return hasWord(d.Message, v.word) && d.Status >= 500 && d.TS >= v.from && d.TS < v.to, ""
		},
	},
	{
		label: "agg: errors per service (status>=500, 1 day)",
		agg:   true,
		sql: func(v variant) string {
			return fmt.Sprintf("SELECT service, COUNT(*) FROM logs WHERE status >= 500 AND ts >= %d AND ts < %d GROUP BY service ORDER BY COUNT(*) DESC LIMIT 20", v.from, v.to+82800)
		},
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":0,"track_total_hits":true,"query":{"bool":{"filter":[{"range":{"status":{"gte":500}}},{"range":{"ts":{"gte":%d,"lt":%d,"format":"epoch_second"}}}]}},"aggs":{"by":{"terms":{"field":"service","size":20}}}}`, v.from, v.to+82800)
		},
		check: func(d doc, v variant) (bool, string) {
			return d.Status >= 500 && d.TS >= v.from && d.TS < v.to+82800, d.Service
		},
	},
	{
		label: "agg: full-text + status histogram (all time)",
		agg:   true,
		sql: func(v variant) string {
			return fmt.Sprintf("SELECT status, COUNT(*) FROM logs WHERE MATCH('%s') GROUP BY status ORDER BY COUNT(*) DESC LIMIT 20", v.word)
		},
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":0,"track_total_hits":true,"query":{"match":{"message":%q}},"aggs":{"by":{"terms":{"field":"status","size":20}}}}`, v.word)
		},
		check: func(d doc, v variant) (bool, string) { return hasWord(d.Message, v.word), strconv.Itoa(d.Status) },
	},
	{
		label: "agg: requests per service, one path, filter only",
		agg:   true,
		sql: func(v variant) string {
			return fmt.Sprintf("SELECT service, COUNT(*) FROM logs WHERE path = '/api/v1/payments' AND status = %d GROUP BY service ORDER BY COUNT(*) DESC LIMIT 20", v.status)
		},
		es: func(v variant) string {
			return fmt.Sprintf(`{"size":0,"track_total_hits":true,"query":{"bool":{"filter":[{"term":{"path":"/api/v1/payments"}},{"term":{"status":%d}}]}},"aggs":{"by":{"terms":{"field":"service","size":20}}}}`, v.status)
		},
		check: func(d doc, v variant) (bool, string) {
			return d.Path == "/api/v1/payments" && d.Status == v.status, d.Service
		},
	},
}

var phrases = [][2]string{{"connection", "refused"}, {"timed", "out"}, {"cache", "miss"}, {"payment", "declined"}, {"logged", "in"}, {"rate", "limit"}, {"deadlock", "detected"}, {"slow", "query"}}

func randVariant(r *rand.Rand) variant {
	p := phrases[r.Intn(len(phrases))]
	from := t0 + r.Int63n(spanSecs-90000)
	return variant{word: words[r.Intn(len(words))], from: from, to: from + 3600,
		status: []int{200, 404, 500, 502, 503, 504}[r.Intn(6)], service: services[r.Intn(len(services))]}.withPhrase(p)
}

func (v variant) withPhrase(p [2]string) variant { v.word2 = p[1]; v.phraseWord = p[0]; return v }

// ---------------------------------------------------------------------------- main

func pct(l []float64, p float64) float64 {
	if len(l) == 0 {
		return math.NaN()
	}
	return l[int(float64(len(l)-1)*p)]
}

func main() {
	total = envInt("DOCS", 2000000)
	days := envInt("DAYS", 7)
	spanSecs = int64(days) * 86400
	writers := envInt("WRITERS", 8)
	batch := envInt("BATCH", 5000)
	runs := envInt("RUNS", 200)
	clients := envInt("CLIENTS", 4)
	only := env("ENGINES", "manticore,elasticsearch")

	db, err := sql.Open("mysql", env("MANTICORE_DSN", "tcp(manticore:9306)/?interpolateParams=true&maxAllowedPacket=0"))
	if err != nil {
		fail("%v", err)
	}
	db.SetMaxOpenConns(64)
	db.SetMaxIdleConns(64)
	es, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{env("ES_URL", "http://elasticsearch:9200")}})
	if err != nil {
		fail("%v", err)
	}
	var engines []engine
	for _, n := range strings.Split(only, ",") {
		switch n {
		case "manticore":
			engines = append(engines, &manticore{db})
		case "elasticsearch":
			engines = append(engines, &elastic{es})
		}
	}
	fmt.Printf("log-search: %d docs over %d days, %d writers x %d docs/request, %d query runs x %d clients\n\n", total, days, writers, batch, runs, clients)

	// 1-3: ingest and size
	for _, e := range engines {
		if err := e.setup(); err != nil {
			fail("%s setup: %v", e.name(), err)
		}
		var next atomic.Int64
		var wg sync.WaitGroup
		lat := make([][]float64, writers)
		start := time.Now()
		for w := 0; w < writers; w++ {
			wg.Add(1)
			go func(w int) {
				defer wg.Done()
				for {
					first := next.Add(int64(batch)) - int64(batch) + 1
					if first > int64(total) {
						return
					}
					n := int64(batch)
					if first+n-1 > int64(total) {
						n = int64(total) - first + 1
					}
					t := time.Now()
					if err := e.ingest(first, n); err != nil {
						fail("%s ingest: %v", e.name(), err)
					}
					lat[w] = append(lat[w], float64(time.Since(t).Milliseconds()))
				}
			}(w)
		}
		wg.Wait()
		acked := time.Since(start)
		if err := e.finish(); err != nil {
			fail("%s finish: %v", e.name(), err)
		}
		settled := time.Since(start)
		var all []float64
		for _, l := range lat {
			all = append(all, l...)
		}
		sort.Float64s(all)
		disk, mem := e.size()
		fmt.Printf("%-13s ingest %d docs: %.1f s acknowledged (%.0f docs/s), %.1f s until searchable and settled (%.0f docs/s); request p50 %.0f ms p99 %.0f ms\n",
			e.name(), total, acked.Seconds(), float64(total)/acked.Seconds(), settled.Seconds(), float64(total)/settled.Seconds(), pct(all, .5), pct(all, .99))
		fmt.Printf("%-13s size: %s; memory: %s\n", e.name(), disk, mem)
	}

	// 4a: correctness on fixed variants: engines vs the generator's own count
	fmt.Println("\ncorrectness (fixed variant of each query; expected = counted from the generator):")
	fixed := variant{word: "timeout", word2: "refused", from: t0 + 2*86400, to: t0 + 2*86400 + 3600, status: 503}
	fixed.phraseWord = "connection"
	bad := 0
	exps := expected(fixed)
	for qi, q := range queries {
		v := fixed
		exp := exps[qi]
		line := fmt.Sprintf("  %-48s expected %8d", q.label, exp.hits)
		for _, e := range engines {
			r, err := e.query(q, v)
			if err != nil {
				fail("%s %s: %v", e.name(), q.label, err)
			}
			ok := r.hits == exp.hits
			if q.agg {
				for k, c := range exp.buckets {
					if r.buckets[k] != c {
						ok = false
					}
				}
				if len(r.buckets) != len(exp.buckets) {
					ok = false
				}
			}
			mark := "ok"
			if !ok {
				mark = "MISMATCH"
				bad++
			}
			line += fmt.Sprintf(" | %s %8d %s", e.name(), r.hits, mark)
		}
		fmt.Println(line)
	}

	// 4b: latency and throughput on randomized variants
	fmt.Printf("\nqueries (%d randomized runs each from %d clients after %d warm-up runs; ms per query)\n", runs, clients, clients*2)
	fmt.Printf("  %-48s %-13s %8s %8s %8s %8s\n", "query", "engine", "q/s", "p50", "p99", "max")
	for _, q := range queries {
		for _, e := range engines {
			for c := 0; c < clients*2; c++ { // warm-up
				e.query(q, randVariant(rand.New(rand.NewSource(int64(c)))))
			}
			var mu sync.Mutex
			var l []float64
			var next atomic.Int64
			var wg sync.WaitGroup
			start := time.Now()
			for c := 0; c < clients; c++ {
				wg.Add(1)
				go func() {
					defer wg.Done()
					for {
						i := next.Add(1)
						if i > int64(runs) {
							return
						}
						v := randVariant(rand.New(rand.NewSource(1000 + i))) // same variants for both engines
						t := time.Now()
						if _, err := e.query(q, v); err != nil {
							fail("%s %s: %v", e.name(), q.label, err)
						}
						ms := float64(time.Since(t).Microseconds()) / 1000
						mu.Lock()
						l = append(l, ms)
						mu.Unlock()
					}
				}()
			}
			wg.Wait()
			el := time.Since(start).Seconds()
			sort.Float64s(l)
			fmt.Printf("  %-48s %-13s %8.0f %8.1f %8.1f %8.1f\n", q.label, e.name(), float64(runs)/el, pct(l, .5), pct(l, .99), l[len(l)-1])
		}
	}
	if bad > 0 {
		fmt.Printf("\nCORRECTNESS FAILED: %d mismatches\n", bad)
		os.Exit(1)
	}
	fmt.Println("\nCORRECTNESS OK: every engine matched the generator's counts")
}

// expected results of every query for variant v, counted from the generator in one parallel pass
func expected(v variant) []result {
	parts := 8
	per := make([][]result, parts)
	var wg sync.WaitGroup
	for p := 0; p < parts; p++ {
		wg.Add(1)
		go func(p int) {
			defer wg.Done()
			res := make([]result, len(queries))
			for i := range res {
				res[i].buckets = map[string]int64{}
			}
			for id := int64(1 + p); id <= int64(total); id += int64(parts) {
				d := gen(id)
				for qi, q := range queries {
					if ok, k := q.check(d, v); ok {
						res[qi].hits++
						if q.agg {
							res[qi].buckets[k]++
						}
					}
				}
			}
			per[p] = res
		}(p)
	}
	wg.Wait()
	out := per[0]
	for _, r := range per[1:] {
		for qi := range r {
			out[qi].hits += r[qi].hits
			for k, c := range r[qi].buckets {
				out[qi].buckets[k] += c
			}
		}
	}
	return out
}
