// Online feature serving against RonDB: two feature groups (user_profile_1,
// user_activity_1) keyed by user_id, and "get the feature vector for these N users"
// requests served five ways: single-row SQL SELECTs, SQL IN lists, one pushed-down SQL
// join, single REST pk-reads and one REST batch request.
//
//	feature-store load     create fs.* and insert -users rows into each feature group
//	feature-store run      serve feature vectors for -duration per mode, print p50/p99 and throughput
//	feature-store failover serve with sql-in + rest-batch and print one line per second
package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/rand/v2"
	"net/http"
	"os"
	"slices"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-sql-driver/mysql"
)

const db = "fs"

// A feature group is one NDB table keyed by user_id, like a Hopsworks online feature
// group (<name>_<version>).
type featureGroup struct {
	table    string
	features []string
	ddl      string
}

var groups = []featureGroup{
	{
		table:    "user_profile_1",
		features: []string{"age", "country", "gender", "account_age_days", "is_premium", "lifetime_value", "avg_order_value", "segment"},
		ddl: `CREATE TABLE IF NOT EXISTS fs.user_profile_1 (
  user_id BIGINT NOT NULL,
  age TINYINT UNSIGNED NOT NULL,
  country SMALLINT NOT NULL,
  gender TINYINT NOT NULL,
  account_age_days INT NOT NULL,
  is_premium TINYINT NOT NULL,
  lifetime_value DOUBLE NOT NULL,
  avg_order_value DOUBLE NOT NULL,
  segment INT NOT NULL,
  PRIMARY KEY (user_id) USING HASH
) ENGINE=NDB`,
	},
	{
		table:    "user_activity_1",
		features: []string{"sessions_7d", "clicks_7d", "cart_adds_7d", "purchases_30d", "avg_session_sec", "ctr_30d", "days_since_purchase", "last_category"},
		ddl: `CREATE TABLE IF NOT EXISTS fs.user_activity_1 (
  user_id BIGINT NOT NULL,
  sessions_7d INT NOT NULL,
  clicks_7d INT NOT NULL,
  cart_adds_7d INT NOT NULL,
  purchases_30d INT NOT NULL,
  avg_session_sec FLOAT NOT NULL,
  ctr_30d FLOAT NOT NULL,
  days_since_purchase INT NOT NULL,
  last_category INT NOT NULL,
  PRIMARY KEY (user_id) USING HASH
) ENGINE=NDB`,
	},
}

// deterministic feature values per user (splitmix64), so reruns load the same data
func mix(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
}

func rowValues(g int, id int64) string {
	h := mix(uint64(id)*2 + uint64(g))
	r := func(n uint64) uint64 { h = mix(h); return h % n }
	f := func() float64 { h = mix(h); return float64(h%1_000_000) / 1_000_000 }
	if g == 0 {
		return fmt.Sprintf("(%d,%d,%d,%d,%d,%d,%.2f,%.2f,%d)", id, 18+r(60), r(200), r(3), r(3650),
			r(2), f()*5000, f()*200, r(32))
	}
	return fmt.Sprintf("(%d,%d,%d,%d,%d,%.1f,%.4f,%d,%d)", id, r(50), r(500), r(40), r(10),
		f()*900, f()*0.2, r(365), r(100))
}

type config struct {
	users       int64
	batch       int
	concurrency int
	duration    time.Duration
	warmup      time.Duration
	modes       string
	loaders     int
	chunk       int
	mysqlDSN    string
	restURL     string
}

func main() {
	if len(os.Args) < 2 {
		fmt.Fprintln(os.Stderr, "usage: feature-store load|run|failover [flags]")
		os.Exit(2)
	}
	cmd := os.Args[1]
	var c config
	fl := flag.NewFlagSet(cmd, flag.ExitOnError)
	fl.Int64Var(&c.users, "users", envInt("USERS", 1_000_000), "users (rows per feature group)")
	fl.IntVar(&c.batch, "batch", int(envInt("BATCH", 16)), "users per feature-vector request")
	fl.IntVar(&c.concurrency, "concurrency", int(envInt("CONCURRENCY", 16)), "concurrent clients")
	fl.DurationVar(&c.duration, "duration", envDur("DURATION", 20*time.Second), "measured time per mode")
	fl.DurationVar(&c.warmup, "warmup", 3*time.Second, "unmeasured warm-up per mode")
	fl.StringVar(&c.modes, "modes", env("MODES", "sql-single,sql-in,sql-join,rest-pk,rest-batch"), "modes to run")
	fl.IntVar(&c.loaders, "loaders", 8, "parallel loaders")
	fl.IntVar(&c.chunk, "chunk", 1000, "rows per INSERT")
	fl.StringVar(&c.mysqlDSN, "mysql", env("MYSQL_DSN", "rondb:rondb@tcp(127.0.0.1:3308)/"), "MySQL DSN")
	fl.StringVar(&c.restURL, "rest", env("REST_URL", "http://127.0.0.1:4407"), "REST API base URL")
	fl.Parse(os.Args[2:])

	var err error
	switch cmd {
	case "load":
		err = load(c)
	case "run":
		err = run(c)
	case "failover":
		err = failover(c)
	default:
		err = fmt.Errorf("unknown command %q", cmd)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func openDB(c config) (*sql.DB, error) {
	d, err := sql.Open("mysql", c.mysqlDSN)
	if err != nil {
		return nil, err
	}
	d.SetMaxOpenConns(c.concurrency + c.loaders)
	d.SetMaxIdleConns(c.concurrency + c.loaders)
	return d, d.Ping()
}

// ---------------------------------------------------------------- load

func load(c config) error {
	d, err := openDB(c)
	if err != nil {
		return err
	}
	defer d.Close()
	if _, err := d.Exec("CREATE DATABASE IF NOT EXISTS " + db); err != nil {
		return err
	}
	for _, g := range groups {
		if _, err := d.Exec(g.ddl); err != nil {
			return err
		}
	}
	for gi, g := range groups {
		var have int64
		d.QueryRow("SELECT COUNT(*) FROM fs." + g.table).Scan(&have)
		if have == c.users {
			fmt.Printf("fs.%s: %d rows already loaded\n", g.table, have)
			continue
		}
		if _, err := d.Exec("TRUNCATE fs." + g.table); err != nil {
			return err
		}
		start := time.Now()
		next := atomic.Int64{}
		var wg sync.WaitGroup
		errc := make(chan error, c.loaders)
		for range c.loaders {
			wg.Add(1)
			go func() {
				defer wg.Done()
				var b strings.Builder
				for {
					from := next.Add(int64(c.chunk)) - int64(c.chunk)
					if from >= c.users {
						return
					}
					b.Reset()
					b.WriteString("INSERT INTO fs." + g.table + " VALUES ")
					for id := from; id < min(from+int64(c.chunk), c.users); id++ {
						if id > from {
							b.WriteByte(',')
						}
						b.WriteString(rowValues(gi, id))
					}
					// NDB temporary errors (MySQL 1297, e.g. 410 redo log overloaded while a
					// checkpoint catches up) succeed when retried
					for try := 1; ; try++ {
						_, err := d.Exec(b.String())
						var me *mysql.MySQLError
						if err != nil && errors.As(err, &me) && me.Number == 1297 && try < 50 {
							time.Sleep(time.Duration(try) * 100 * time.Millisecond)
							continue
						}
						if err != nil {
							errc <- err
							return
						}
						break
					}
				}
			}()
		}
		wg.Wait()
		close(errc)
		if err := <-errc; err != nil {
			return err
		}
		el := time.Since(start)
		fmt.Printf("fs.%s: %d rows in %.1f s (%.0f rows/s, %d loaders x %d-row INSERTs)\n",
			g.table, c.users, el.Seconds(), float64(c.users)/el.Seconds(), c.loaders, c.chunk)
	}
	return nil
}

// ---------------------------------------------------------------- serving

// a server fetches the feature vectors (both feature groups) for one batch of user ids
// and returns the number of rows it got back
type server interface {
	fetch(ctx context.Context, ids []int64) (int, error)
}

type modeInfo struct {
	name, desc string
	make       func(c config, d *sql.DB, h *http.Client) (server, error)
}

var allModes = []modeInfo{
	{"sql-single", "1 SELECT ... WHERE user_id = ? per user and feature group (prepared)", newSQLSingle},
	{"sql-in", "1 SELECT ... WHERE user_id IN (...) per feature group (prepared)", newSQLIn},
	{"sql-join", "1 SELECT joining both feature groups, user_id IN (...) (pushed join)", newSQLJoin},
	{"rest-pk", "1 REST pk-read per user and feature group", newRESTPk},
	{"rest-batch", "1 REST batch request with every pk-read of the vector", newRESTBatch},
}

func cols(g featureGroup, prefix string) string {
	out := make([]string, len(g.features))
	for i, f := range g.features {
		out[i] = prefix + f
	}
	return strings.Join(out, ", ")
}

func placeholders(n int) string { return strings.TrimSuffix(strings.Repeat("?,", n), ",") }

func toArgs(ids []int64) []any {
	a := make([]any, len(ids))
	for i, id := range ids {
		a[i] = id
	}
	return a
}

// countRows drains rows into scratch columns and counts them
func countRows(rows *sql.Rows) (int, error) {
	defer rows.Close()
	cs, _ := rows.Columns()
	dst := make([]any, len(cs))
	for i := range dst {
		dst[i] = new(sql.RawBytes)
	}
	n := 0
	for rows.Next() {
		if err := rows.Scan(dst...); err != nil {
			return n, err
		}
		n++
	}
	return n, rows.Err()
}

type sqlSingle struct{ stmts []*sql.Stmt }

func newSQLSingle(c config, d *sql.DB, _ *http.Client) (server, error) {
	s := &sqlSingle{}
	for _, g := range groups {
		st, err := d.Prepare("SELECT " + cols(g, "") + " FROM fs." + g.table + " WHERE user_id = ?")
		if err != nil {
			return nil, err
		}
		s.stmts = append(s.stmts, st)
	}
	return s, nil
}

func (s *sqlSingle) fetch(ctx context.Context, ids []int64) (int, error) {
	n := 0
	for _, id := range ids {
		for _, st := range s.stmts {
			rows, err := st.QueryContext(ctx, id)
			if err != nil {
				return n, err
			}
			k, err := countRows(rows)
			n += k
			if err != nil {
				return n, err
			}
		}
	}
	return n, nil
}

type sqlStmts struct{ stmts []*sql.Stmt }

func newSQLIn(c config, d *sql.DB, _ *http.Client) (server, error) {
	s := &sqlStmts{}
	for _, g := range groups {
		st, err := d.Prepare("SELECT user_id, " + cols(g, "") + " FROM fs." + g.table +
			" WHERE user_id IN (" + placeholders(c.batch) + ")")
		if err != nil {
			return nil, err
		}
		s.stmts = append(s.stmts, st)
	}
	return s, nil
}

func newSQLJoin(c config, d *sql.DB, _ *http.Client) (server, error) {
	st, err := d.Prepare("SELECT p.user_id, " + cols(groups[0], "p.") + ", " + cols(groups[1], "a.") +
		" FROM fs.user_profile_1 p JOIN fs.user_activity_1 a ON a.user_id = p.user_id" +
		" WHERE p.user_id IN (" + placeholders(c.batch) + ")")
	if err != nil {
		return nil, err
	}
	return &sqlStmts{stmts: []*sql.Stmt{st}}, nil
}

func (s *sqlStmts) fetch(ctx context.Context, ids []int64) (int, error) {
	n := 0
	args := toArgs(ids)
	for _, st := range s.stmts {
		rows, err := st.QueryContext(ctx, args...)
		if err != nil {
			return n, err
		}
		k, err := countRows(rows)
		n += k
		if err != nil {
			return n, err
		}
	}
	if len(s.stmts) == 1 { // a joined row carries both feature groups
		n *= len(groups)
	}
	return n, nil
}

type pkReadBody struct {
	Filters     []filter `json:"filters"`
	ReadColumns []column `json:"readColumns"`
	OperationID string   `json:"operationId,omitempty"`
}
type filter struct {
	Column string `json:"column"`
	Value  int64  `json:"value"`
}
type column struct {
	Column string `json:"column"`
}

func readCols(g featureGroup) []column {
	out := make([]column, len(g.features))
	for i, f := range g.features {
		out[i] = column{f}
	}
	return out
}

type restServer struct {
	base string
	h    *http.Client
}

func (r *restServer) post(ctx context.Context, path string, body any) ([]byte, error) {
	b, _ := json.Marshal(body)
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, r.base+path, bytes.NewReader(b))
	req.Header.Set("Content-Type", "application/json")
	resp, err := r.h.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d: %s", resp.StatusCode, bytes.TrimSpace(out))
	}
	return out, nil
}

type restPk struct{ restServer }

func newRESTPk(c config, _ *sql.DB, h *http.Client) (server, error) {
	return &restPk{restServer{c.restURL, h}}, nil
}

func (r *restPk) fetch(ctx context.Context, ids []int64) (int, error) {
	n := 0
	for _, id := range ids {
		for _, g := range groups {
			out, err := r.post(ctx, "/0.1.0/"+db+"/"+g.table+"/pk-read",
				pkReadBody{Filters: []filter{{"user_id", id}}, ReadColumns: readCols(g)})
			if err != nil {
				return n, err
			}
			var res struct {
				Data map[string]json.RawMessage `json:"data"`
			}
			if err := json.Unmarshal(out, &res); err != nil {
				return n, err
			}
			if len(res.Data) == len(g.features) {
				n++
			}
		}
	}
	return n, nil
}

type restBatch struct{ restServer }

type batchOp struct {
	Method      string     `json:"method"`
	RelativeURL string     `json:"relative-url"`
	Body        pkReadBody `json:"body"`
}

func newRESTBatch(c config, _ *sql.DB, h *http.Client) (server, error) {
	return &restBatch{restServer{c.restURL, h}}, nil
}

func (r *restBatch) fetch(ctx context.Context, ids []int64) (int, error) {
	ops := make([]batchOp, 0, len(ids)*len(groups))
	for _, id := range ids {
		for _, g := range groups {
			ops = append(ops, batchOp{"POST", db + "/" + g.table + "/pk-read",
				pkReadBody{Filters: []filter{{"user_id", id}}, ReadColumns: readCols(g),
					OperationID: g.table + ":" + strconv.FormatInt(id, 10)}})
		}
	}
	out, err := r.post(ctx, "/0.1.0/batch", map[string]any{"operations": ops})
	if err != nil {
		return 0, err
	}
	type sub struct {
		Code int `json:"code"`
		Body struct {
			Data map[string]json.RawMessage `json:"data"`
		} `json:"body"`
	}
	var res []sub
	if err := json.Unmarshal(out, &res); err != nil {
		var wrapped struct {
			Result []sub `json:"result"`
		}
		if err2 := json.Unmarshal(out, &wrapped); err2 != nil {
			return 0, err
		}
		res = wrapped.Result
	}
	n := 0
	for _, s := range res {
		if s.Code != http.StatusOK {
			return n, fmt.Errorf("batch sub-operation: code %d", s.Code)
		}
		if len(s.Body.Data) > 0 {
			n++
		}
	}
	return n, nil
}

func httpClient(conns int) *http.Client {
	t := http.DefaultTransport.(*http.Transport).Clone()
	t.MaxIdleConns = conns * 2
	t.MaxIdleConnsPerHost = conns * 2
	t.MaxConnsPerHost = conns * 2
	return &http.Client{Transport: t, Timeout: 5 * time.Second}
}

// randomIDs fills ids with distinct random users (an IN list returns each row once)
func randomIDs(rng *rand.Rand, ids []int64, users int64) {
	for i := range ids {
		for {
			ids[i] = rng.Int64N(users)
			if !slices.Contains(ids[:i], ids[i]) {
				break
			}
		}
	}
}

// ---------------------------------------------------------------- run

type result struct {
	mode       modeInfo
	lat        []time.Duration
	rows, errs int64
	firstErr   error
	elapsed    time.Duration
}

func run(c config) error {
	d, err := openDB(c)
	if err != nil {
		return err
	}
	defer d.Close()
	h := httpClient(c.concurrency)
	fmt.Printf("feature vector = %d users x %d feature groups (%d features each) out of %d users; %d concurrent clients, %s per mode\n\n",
		c.batch, len(groups), len(groups[0].features), c.users, c.concurrency, c.duration)

	var results []result
	for _, name := range strings.Split(c.modes, ",") {
		i := slices.IndexFunc(allModes, func(m modeInfo) bool { return m.name == name })
		if i < 0 {
			return fmt.Errorf("unknown mode %q", name)
		}
		m := allModes[i]
		srv, err := m.make(c, d, h)
		if err != nil {
			return fmt.Errorf("%s: %w", name, err)
		}
		r := measure(c, m, srv)
		results = append(results, r)
	}

	fmt.Printf("%-10s  %10s  %10s  %7s  %7s  %8s  %6s  %s\n", "mode", "vectors/s", "rows/s", "p50 ms", "p99 ms", "p99.9 ms", "errors", "per feature vector")
	for _, r := range results {
		slices.Sort(r.lat)
		secs := r.elapsed.Seconds()
		fmt.Printf("%-10s  %10.0f  %10.0f  %7.2f  %7.2f  %8.2f  %6d  %s\n", r.mode.name,
			float64(len(r.lat))/secs, float64(r.rows)/secs, ms(pct(r.lat, 50)), ms(pct(r.lat, 99)),
			ms(pct(r.lat, 99.9)), r.errs, r.mode.desc)
		if r.firstErr != nil {
			fmt.Printf("            first error: %v\n", r.firstErr)
		}
	}
	return nil
}

func measure(c config, m modeInfo, srv server) result {
	ctx := context.Background()
	res := result{mode: m}
	var mu sync.Mutex
	var wg sync.WaitGroup
	start := time.Now()
	measureFrom := start.Add(c.warmup)
	end := measureFrom.Add(c.duration)
	want := c.batch * len(groups)
	for w := range c.concurrency {
		wg.Add(1)
		go func() {
			defer wg.Done()
			rng := rand.New(rand.NewPCG(uint64(w), uint64(time.Now().UnixNano())))
			ids := make([]int64, c.batch)
			lat := make([]time.Duration, 0, 1<<16)
			var rows, errs int64
			var first error
			for {
				randomIDs(rng, ids, c.users)
				t0 := time.Now()
				if t0.After(end) {
					break
				}
				n, err := srv.fetch(ctx, ids)
				if err == nil && n != want {
					err = fmt.Errorf("got %d of %d feature rows", n, want)
				}
				if t0.Before(measureFrom) {
					continue
				}
				if err != nil {
					errs++
					if first == nil {
						first = err
					}
					continue
				}
				lat = append(lat, time.Since(t0))
				rows += int64(n)
			}
			mu.Lock()
			res.lat = append(res.lat, lat...)
			res.rows += rows
			res.errs += errs
			if res.firstErr == nil {
				res.firstErr = first
			}
			mu.Unlock()
		}()
	}
	wg.Wait()
	res.elapsed = c.duration
	return res
}

func pct(s []time.Duration, p float64) time.Duration {
	if len(s) == 0 {
		return 0
	}
	i := int(float64(len(s))*p/100+0.5) - 1
	return s[max(0, min(i, len(s)-1))]
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }

// ---------------------------------------------------------------- failover

// failover serves with sql-in and rest-batch side by side (half the clients each) and
// prints one line per second (vectors finished in that second, slowest one including
// retries); a failed request is retried up to 3 times (10 ms apart)
// and only counts as failed when every attempt failed.
func failover(c config) error {
	d, err := openDB(c)
	if err != nil {
		return err
	}
	defer d.Close()
	h := httpClient(c.concurrency)
	names := []string{"sql-in", "rest-batch"}
	srvs := make([]server, len(names))
	for i, name := range names {
		m := allModes[slices.IndexFunc(allModes, func(m modeInfo) bool { return m.name == name })]
		if srvs[i], err = m.make(c, d, h); err != nil {
			return err
		}
	}
	type sec struct {
		ok, retried, failed [2]atomic.Int64
		maxLat              [2]atomic.Int64
	}
	total := int(c.duration / time.Second)
	secs := make([]sec, total+1)
	errMsgs := sync.Map{}
	start := time.Now()
	end := start.Add(c.duration)
	want := c.batch * len(groups)
	var wg sync.WaitGroup
	for w := range c.concurrency {
		wg.Add(1)
		go func() {
			defer wg.Done()
			k := w % 2
			rng := rand.New(rand.NewPCG(uint64(w), uint64(time.Now().UnixNano())))
			ids := make([]int64, c.batch)
			for {
				randomIDs(rng, ids, c.users)
				t0 := time.Now()
				if t0.After(end) {
					return
				}
				var err error
				attempts := 0
				for attempts < 4 {
					attempts++
					ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
					var n int
					n, err = srvs[k].fetch(ctx, ids)
					cancel()
					if err == nil && n != want {
						err = fmt.Errorf("got %d of %d feature rows", n, want)
					}
					if err == nil {
						break
					}
					msg := err.Error()
					if len(msg) > 160 {
						msg = msg[:160]
					}
					errMsgs.LoadOrStore(names[k]+": "+msg, t0.Sub(start).Truncate(time.Second))
					time.Sleep(10 * time.Millisecond)
				}
				// counted in the second the vector was served (or finally failed)
				done := time.Now()
				lat := done.Sub(t0)
				s := &secs[min(int(done.Sub(start)/time.Second), total)]
				for {
					cur := s.maxLat[k].Load()
					if int64(lat) <= cur || s.maxLat[k].CompareAndSwap(cur, int64(lat)) {
						break
					}
				}
				switch {
				case err != nil:
					s.failed[k].Add(1)
				case attempts > 1:
					s.retried[k].Add(1)
					s.ok[k].Add(1)
				default:
					s.ok[k].Add(1)
				}
			}
		}()
	}

	fmt.Printf("%d clients (half sql-in, half rest-batch), %d users per vector, %s; ok = vectors served, retried = ok after 1-3 retries, failed = all 4 attempts failed\n",
		c.concurrency, c.batch, c.duration)
	fmt.Printf("%-8s  %3s  %-25s  %s\n", "UTC", "s", "sql-in ok/retr/fail max", "rest-batch ok/retr/fail max")
	for i := range total {
		time.Sleep(time.Until(start.Add(time.Duration(i+1) * time.Second).Add(20 * time.Millisecond)))
		s := &secs[i]
		line := fmt.Sprintf("%s  %3d", start.Add(time.Duration(i)*time.Second).UTC().Format("15:04:05"), i)
		for k := range 2 {
			line += fmt.Sprintf("  %6d/%3d/%3d %5.0fms", s.ok[k].Load(), s.retried[k].Load(), s.failed[k].Load(),
				ms(time.Duration(s.maxLat[k].Load())))
		}
		fmt.Println(line)
	}
	wg.Wait()
	var tot [2][3]int64
	for i := range secs {
		for k := range 2 {
			tot[k][0] += secs[i].ok[k].Load()
			tot[k][1] += secs[i].retried[k].Load()
			tot[k][2] += secs[i].failed[k].Load()
		}
	}
	for k, name := range names {
		fmt.Printf("%-10s total: %d vectors served, %d needed a retry, %d failed\n", name, tot[k][0], tot[k][1], tot[k][2])
	}
	var msgs []string
	errMsgs.Range(func(k, v any) bool {
		msgs = append(msgs, fmt.Sprintf("  first at %3ds  %s", int(v.(time.Duration).Seconds()), k))
		return true
	})
	sort.Strings(msgs)
	if len(msgs) > 0 {
		fmt.Println("distinct errors seen (before retry):")
		for _, m := range msgs {
			fmt.Println(m)
		}
	}
	return nil
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int64) int64 {
	if v, err := strconv.ParseInt(os.Getenv(k), 10, 64); err == nil {
		return v
	}
	return def
}

func envDur(k string, def time.Duration) time.Duration {
	if v, err := time.ParseDuration(os.Getenv(k)); err == nil {
		return v
	}
	return def
}
