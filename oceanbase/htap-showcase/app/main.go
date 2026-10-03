// htap-showcase: OLTP writes and real-time analytics on one OceanBase table.
//
// One hybrid row/column table (`WITH COLUMN GROUP(all columns, each column)`) takes
// transactional writes; the same table answers analytical aggregations either through
// its row store (NO_USE_COLUMN_TABLE hint) or its column store (USE_COLUMN_TABLE).
//
// Phases:
//  1. load ROWS orders, then a major compaction (builds the columnar baseline)
//  2. analytics alone: each query via row store, column store, column store + PARALLEL
//  3. OLTP alone (baseline tps / latency)
//  4. OLTP + analytics loop via the row store
//  5. OLTP + analytics loop via the column store, with a freshness probe
package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math/rand/v2"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/go-sql-driver/mysql"
)

var (
	host          = env("OB_HOST", "oceanbase:2881")
	user          = env("OB_USER", "root@test")
	rows          = envInt("ROWS", 2_000_000)
	duration      = time.Duration(envInt("DURATION", 30)) * time.Second
	oltpWorkers   = envInt("OLTP_WORKERS", 16)
	olapWorkers   = envInt("OLAP_WORKERS", 2)
	parallelDOP   = envInt("DOP", 4)
	loadWorkers   = envInt("LOAD_WORKERS", 8)
	soloRepeats   = envInt("REPEATS", 3)
	regions       = []string{"north", "south", "east", "west", "central", "apac", "emea", "latam"}
	nextID        atomic.Int64
	loadBatchRows = 1000
)

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func envInt(k string, d int) int {
	if v := os.Getenv(k); v != "" {
		n, err := strconv.Atoi(v)
		if err != nil {
			panic(fmt.Sprintf("%s=%q: %v", k, v, err))
		}
		return n
	}
	return d
}

func open(u, db string, conns int) *sql.DB {
	cfg := mysql.NewConfig()
	cfg.User = u
	cfg.Net = "tcp"
	cfg.Addr = host
	cfg.DBName = db
	cfg.InterpolateParams = true
	cfg.ClientFoundRows = true // UPDATE reports matched rows, not changed rows
	// OceanBase defaults: ob_query_timeout 10 s, too short for row-store scans of the whole table
	cfg.Params = map[string]string{"ob_query_timeout": "300000000", "ob_trx_timeout": "300000000"}
	c, err := mysql.NewConnector(cfg)
	must(err)
	d := sql.OpenDB(c)
	d.SetMaxOpenConns(conns)
	d.SetMaxIdleConns(conns)
	return d
}

func must(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

func main() {
	admin := open(user, "", 2)
	must(admin.Ping())
	var version string
	must(admin.QueryRow("SELECT version()").Scan(&version))
	fmt.Printf("OceanBase %s at %s as %s\n", version, host, user)
	_, err := admin.Exec("CREATE DATABASE IF NOT EXISTS htap")
	must(err)
	admin.Close()

	db := open(user, "htap", oltpWorkers+olapWorkers+loadWorkers+4)
	defer db.Close()

	header("1. schema, load, major compaction")
	setup(db)

	header("2. analytics alone (median of " + strconv.Itoa(soloRepeats) + " runs)")
	showPlans(db)
	soloAnalytics(db)

	header("3. OLTP alone")
	runMixed(db, nil, "", false) // warm-up run of the same length, not reported
	base := runMixed(db, nil, "", false)
	base.print("OLTP only")

	header("4. OLTP + analytics through the ROW store")
	rowRes := runMixed(db, db, "NO_USE_COLUMN_TABLE(orders)", false)
	rowRes.print("OLTP + row-store analytics")

	header("5. OLTP + analytics through the COLUMN store")
	colRes := runMixed(db, db, "USE_COLUMN_TABLE(orders)", true)
	colRes.print("OLTP + column-store analytics")

	header("summary")
	fmt.Printf("%-34s %9s %9s %9s %9s %12s %12s\n", "phase", "OLTP tps", "p50 ms", "p99 ms", "vs alone", "OLAP q/min", "OLAP p50 s")
	for _, r := range []struct {
		n string
		m *mixed
	}{{"OLTP alone", base}, {"OLTP + row-store analytics", rowRes}, {"OLTP + column-store analytics", colRes}} {
		olap, olapP50 := "-", "-"
		if r.m.olapN > 0 {
			olap = fmt.Sprintf("%.0f", float64(r.m.olapN)/r.m.elapsed.Minutes())
			olapP50 = fmt.Sprintf("%.2f", pct(r.m.olapLat, 50).Seconds())
		}
		fmt.Printf("%-34s %9.0f %9.2f %9.2f %8.0f%% %12s %12s\n", r.n, r.m.tps(), ms(pct(r.m.oltpLat, 50)), ms(pct(r.m.oltpLat, 99)),
			100*r.m.tps()/base.tps(), olap, olapP50)
	}
}

func header(s string) { fmt.Printf("\n== %s ==\n", s) }

// ---------------------------------------------------------------- schema + load

const ddl = `CREATE TABLE orders (
  id          BIGINT        NOT NULL,
  customer_id INT           NOT NULL,
  product_id  INT           NOT NULL,
  region      VARCHAR(16)   NOT NULL,
  status      TINYINT       NOT NULL,
  qty         INT           NOT NULL,
  amount      DECIMAL(12,2) NOT NULL,
  created_at  DATETIME(6)   NOT NULL,
  ship_addr   VARCHAR(128)  NOT NULL,
  note        VARCHAR(128)  NOT NULL,
  PRIMARY KEY (id)
) PARTITION BY HASH(id) PARTITIONS 8
  WITH COLUMN GROUP (all columns, each column)`

func setup(db *sql.DB) {
	_, err := db.Exec("DROP TABLE IF EXISTS orders")
	must(err)
	_, err = db.Exec("PURGE RECYCLEBIN") // a dropped table otherwise keeps its tablets
	must(err)
	_, err = db.Exec(ddl)
	must(err)
	fmt.Println("orders: 10 columns, HASH(id) 8 partitions, WITH COLUMN GROUP (all columns, each column)")
	fmt.Println("        = one row-store copy (all columns) + one column group per column, kept in sync by the engine")

	start := time.Now()
	batches := (rows + loadBatchRows - 1) / loadBatchRows
	ch := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < loadWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := rand.New(rand.NewPCG(uint64(w), 42))
			for b := range ch {
				lo := b*loadBatchRows + 1
				hi := min(lo+loadBatchRows-1, rows)
				var sb strings.Builder
				sb.WriteString("INSERT INTO orders VALUES ")
				args := make([]any, 0, (hi-lo+1)*10)
				for id := lo; id <= hi; id++ {
					if id > lo {
						sb.WriteByte(',')
					}
					sb.WriteString("(?,?,?,?,?,?,?,?,?,?)")
					// spread historic orders over the last 30 days
					ts := time.Now().Add(-time.Duration(r.Int64N(int64(30 * 24 * time.Hour))))
					args = append(args, orderArgs(r, int64(id), ts, "")...)
				}
				if _, err := db.Exec(sb.String(), args...); err != nil {
					must(fmt.Errorf("load batch %d: %w", b, err))
				}
			}
		}()
	}
	for b := 0; b < batches; b++ {
		ch <- b
		if (b+1)%(batches/10+1) == 0 {
			fmt.Printf("  loaded %d / %d rows\n", min((b+1)*loadBatchRows, rows), rows)
		}
	}
	close(ch)
	wg.Wait()
	nextID.Store(int64(rows))
	el := time.Since(start)
	fmt.Printf("loaded %d rows in %.1fs (%.0f rows/s, %d workers x %d-row INSERTs)\n", rows, el.Seconds(), float64(rows)/el.Seconds(), loadWorkers, loadBatchRows)

	// Writes land in the row-format memtable; a major compaction writes the baseline
	// SSTables, including the per-column groups. Later writes stay row-format until the
	// next compaction and are merged into column-store scans at read time.
	// A tablet-level major freeze compacts just the 8 orders tablets (a tenant-wide
	// `ALTER SYSTEM MAJOR FREEZE` also rewrites ~800 system tablets and took ~6 minutes).
	var dbNow string
	must(db.QueryRow("SELECT CAST(NOW(6) AS CHAR)").Scan(&dbNow))
	start = time.Now()
	var tablets []string
	rs, err := db.Query("SELECT TABLET_ID FROM oceanbase.DBA_OB_TABLE_LOCATIONS WHERE DATABASE_NAME = 'htap' AND TABLE_NAME = 'orders' AND ROLE = 'LEADER'")
	must(err)
	for rs.Next() {
		var t string
		must(rs.Scan(&t))
		tablets = append(tablets, t)
	}
	must(rs.Err())
	rs.Close()
	for _, t := range tablets {
		for {
			_, err := db.Exec("ALTER SYSTEM MAJOR FREEZE TABLET_ID = " + t)
			var me *mysql.MySQLError
			if errors.As(err, &me) && me.Number == 4213 { // last major freeze not finished: retry
				time.Sleep(5 * time.Second)
				continue
			}
			must(err)
			break
		}
	}
	in := strings.Join(tablets, ",")
	for {
		time.Sleep(2 * time.Second)
		var done int
		var mb float64
		must(db.QueryRow(`SELECT COUNT(DISTINCT TABLET_ID), COALESCE(ROUND(SUM(OCCUPY_SIZE)/1024/1024, 1), 0)
			FROM oceanbase.GV$OB_TABLET_COMPACTION_HISTORY
			WHERE TABLET_ID IN (`+in+`) AND TYPE IN ('MEDIUM_MERGE', 'MAJOR_MERGE') AND FINISH_TIME > ?`, dbNow).Scan(&done, &mb))
		if done == len(tablets) {
			fmt.Printf("major compaction of %d tablets (row copy + column groups) done in %.1fs, %.1f MB on disk\n",
				done, time.Since(start).Seconds(), mb)
			return
		}
		if time.Since(start) > 15*time.Minute {
			must(errors.New("tablet major compaction did not finish in 15 minutes"))
		}
	}
}

func orderArgs(r *rand.Rand, id int64, ts time.Time, region string) []any {
	if region == "" {
		region = regions[r.IntN(len(regions))]
	}
	qty := 1 + r.IntN(5)
	return []any{id, r.IntN(100_000), r.IntN(5_000), region, r.IntN(3), qty,
		fmt.Sprintf("%d.%02d", qty*(5+r.IntN(200)), r.IntN(100)), ts,
		fmt.Sprintf("%d Example Street, Apt %d, City %d", r.IntN(9999), r.IntN(500), r.IntN(1000)),
		fmt.Sprintf("gift wrap: %v; leave at door: %v; ref %x", r.IntN(2) == 1, r.IntN(2) == 1, r.Uint64())}
}

// ---------------------------------------------------------------- analytics

type query struct{ name, sql string }

// %s is the hint list (row/column store, parallelism)
var queries = []query{
	{"revenue by region", `SELECT /*+ %s */ region, COUNT(*), SUM(amount), AVG(qty) FROM orders GROUP BY region`},
	{"top 10 products", `SELECT /*+ %s */ product_id, SUM(amount) AS rev FROM orders GROUP BY product_id ORDER BY rev DESC LIMIT 10`},
	{"last 10 min by status", `SELECT /*+ %s */ status, COUNT(*), SUM(amount) FROM orders WHERE created_at >= NOW() - INTERVAL 10 MINUTE GROUP BY status`},
}

func runQuery(ctx context.Context, db *sql.DB, q string) (time.Duration, error) {
	start := time.Now()
	rs, err := db.QueryContext(ctx, q)
	if err != nil {
		return 0, err
	}
	defer rs.Close()
	for rs.Next() {
	}
	return time.Since(start), rs.Err()
}

func showPlans(db *sql.DB) {
	for _, h := range []string{"NO_USE_COLUMN_TABLE(orders)", "USE_COLUMN_TABLE(orders)"} {
		rs, err := db.Query("EXPLAIN " + fmt.Sprintf(queries[0].sql, h))
		must(err)
		op := "?"
		for rs.Next() {
			var line string
			must(rs.Scan(&line))
			for _, k := range []string{"COLUMN TABLE FULL SCAN", "TABLE FULL SCAN"} {
				if strings.Contains(line, k) {
					op = k
					break
				}
			}
			if op != "?" {
				break
			}
		}
		rs.Close()
		fmt.Printf("plan with /*+ %-28s */ -> %s\n", h, op)
	}
}

func soloAnalytics(db *sql.DB) {
	variants := []struct{ name, hint string }{
		{"row store", "NO_USE_COLUMN_TABLE(orders)"},
		{"column store", "USE_COLUMN_TABLE(orders)"},
		{fmt.Sprintf("column + PARALLEL(%d)", parallelDOP), fmt.Sprintf("USE_COLUMN_TABLE(orders) PARALLEL(%d)", parallelDOP)},
	}
	fmt.Printf("%-24s", "query")
	for _, v := range variants {
		fmt.Printf(" %22s", v.name)
	}
	fmt.Printf(" %9s\n", "row/col")
	for _, q := range queries {
		fmt.Printf("%-24s", q.name)
		var med []time.Duration
		for _, v := range variants {
			s := fmt.Sprintf(q.sql, v.hint)
			_, err := runQuery(context.Background(), db, s) // warm-up
			must(err)
			var ts []time.Duration
			for i := 0; i < soloRepeats; i++ {
				d, err := runQuery(context.Background(), db, s)
				must(err)
				ts = append(ts, d)
			}
			m := pct(ts, 50)
			med = append(med, m)
			fmt.Printf(" %20.0fms", ms(m))
		}
		fmt.Printf(" %8.1fx\n", float64(med[0])/float64(med[1]))
	}
}

// ---------------------------------------------------------------- mixed workload

type mixed struct {
	elapsed          time.Duration
	oltpN, oltpErr   int
	oltpLat, olapLat []time.Duration
	olapN, olapErr   int
	firstErr         error
}

func (m *mixed) tps() float64 { return float64(m.oltpN) / m.elapsed.Seconds() }

func (m *mixed) print(name string) {
	fmt.Printf("%s: %d txns in %.0fs = %.0f tps, latency p50 %.2f ms p95 %.2f ms p99 %.2f ms, %d errors\n",
		name, m.oltpN, m.elapsed.Seconds(), m.tps(), ms(pct(m.oltpLat, 50)), ms(pct(m.oltpLat, 95)), ms(pct(m.oltpLat, 99)), m.oltpErr)
	if m.olapN > 0 || m.olapErr > 0 {
		fmt.Printf("  analytics: %d queries (%d workers) p50 %.2f s p95 %.2f s, %d errors\n",
			m.olapN, olapWorkers, pct(m.olapLat, 50).Seconds(), pct(m.olapLat, 95).Seconds(), m.olapErr)
	}
	if m.firstErr != nil {
		fmt.Printf("  first error: %v\n", m.firstErr)
	}
}

// one OLTP transaction: insert a new order, advance the status of one of the last 10,000
// orders, read it back by primary key
func oltpTxn(ctx context.Context, db *sql.DB, r *rand.Rand) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	id := nextID.Add(1)
	if _, err := tx.ExecContext(ctx, "INSERT INTO orders VALUES (?,?,?,?,?,?,?,?,?,?)", orderArgs(r, id, time.Now(), "")...); err != nil {
		return err
	}
	other := id - 1 - r.Int64N(min(id-1, 10_000)) // orders change status while they are recent
	res, err := tx.ExecContext(ctx, "UPDATE orders SET status = LEAST(status + 1, 2) WHERE id = ?", other)
	if err != nil {
		return err
	}
	if n, _ := res.RowsAffected(); n == 0 { // id taken by a transaction that has not committed yet
		return tx.Commit()
	}
	var st int
	if err := tx.QueryRowContext(ctx, "SELECT status FROM orders WHERE id = ?", other).Scan(&st); err != nil {
		return err
	}
	return tx.Commit()
}

// runMixed runs OLTP workers on db for DURATION; with olapDB set, OLAP_WORKERS loop the
// analytic queries with the given hint on olapDB at the same time.
func runMixed(db, olapDB *sql.DB, hint string, probe bool) *mixed {
	ctx, cancel := context.WithTimeout(context.Background(), duration)
	defer cancel()
	res := &mixed{}
	var mu sync.Mutex
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < oltpWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := rand.New(rand.NewPCG(uint64(time.Now().UnixNano()), uint64(w)))
			var lat []time.Duration
			n, e := 0, 0
			var first error
			for ctx.Err() == nil {
				t := time.Now()
				if err := oltpTxn(ctx, db, r); err != nil {
					if ctx.Err() == nil {
						e++
						if first == nil {
							first = err
						}
					}
					continue
				}
				lat = append(lat, time.Since(t))
				n++
			}
			mu.Lock()
			res.oltpN += n
			res.oltpErr += e
			res.oltpLat = append(res.oltpLat, lat...)
			if res.firstErr == nil {
				res.firstErr = first
			}
			mu.Unlock()
		}()
	}
	if olapDB != nil {
		for w := 0; w < olapWorkers; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				var lat []time.Duration
				e := 0
				for i := w; ctx.Err() == nil; i++ {
					d, err := runQuery(ctx, olapDB, fmt.Sprintf(queries[i%len(queries)].sql, hint))
					if err != nil {
						if ctx.Err() == nil {
							e++
						}
						continue
					}
					lat = append(lat, d)
				}
				mu.Lock()
				res.olapN += len(lat)
				res.olapErr += e
				res.olapLat = append(res.olapLat, lat...)
				mu.Unlock()
			}()
		}
	}
	if probe {
		time.Sleep(duration / 2)
		freshness(db, hint)
	}
	wg.Wait()
	res.elapsed = time.Since(start)
	return res
}

// freshness commits a batch of orders in a new region and immediately aggregates over
// the column store: the very next analytic query already counts them.
func freshness(db *sql.DB, hint string) {
	region := fmt.Sprintf("probe%d", time.Now().Unix()%100000)
	const n = 500
	r := rand.New(rand.NewPCG(7, 7))
	var sb strings.Builder
	sb.WriteString("INSERT INTO orders VALUES ")
	args := make([]any, 0, n*10)
	for i := 0; i < n; i++ {
		if i > 0 {
			sb.WriteByte(',')
		}
		sb.WriteString("(?,?,?,?,?,?,?,?,?,?)")
		args = append(args, orderArgs(r, nextID.Add(1), time.Now(), region)...)
	}
	_, err := db.Exec(sb.String(), args...)
	must(err)
	committed := time.Now()
	var cnt int
	var sum float64
	q := fmt.Sprintf("SELECT /*+ %s */ COUNT(*), COALESCE(SUM(amount),0) FROM orders WHERE region = ?", hint)
	must(db.QueryRow(q, region).Scan(&cnt, &sum))
	fmt.Printf("  freshness: committed %d orders in new region %q; the next column-store query (started at commit, took %.0f ms) counted %d of them, sum %.2f\n",
		n, region, ms(time.Since(committed)), cnt, sum)
	var total int64
	must(db.QueryRow(fmt.Sprintf("SELECT /*+ %s */ COUNT(*) FROM orders", hint)).Scan(&total))
	fmt.Printf("  freshness: column-store COUNT(*) = %d, including %d orders inserted after the compaction (row-format memtable / minor SSTables, merged at read time)\n", total, total-int64(rows))
}

// ---------------------------------------------------------------- stats

func pct(d []time.Duration, p float64) time.Duration {
	if len(d) == 0 {
		return 0
	}
	s := append([]time.Duration(nil), d...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	i := int(float64(len(s)-1) * p / 100)
	return s[i]
}

func ms(d time.Duration) float64 { return float64(d) / float64(time.Millisecond) }
