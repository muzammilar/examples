// Order metrics kept fresh by RisingWave from Postgres CDC plus an event stream.
//
// Postgres holds the OLTP tables (customers, products, orders). RisingWave ingests them with the
// postgres-cdc connector and takes page-view events directly (INSERT into an append-only table).
// Two materialized views: revenue per region x category (3-way join + aggregate) and a
// per-product funnel (page views joined with orders). The program measures how fresh the MVs are
// under OLTP load and compares with re-running the same query on Postgres periodically, then
// checks that the MV equals the Postgres query.
package main

import (
	"context"
	"errors"
	"fmt"
	"math/rand/v2"
	"os"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	customers  = 10000
	products   = 1000
	regions    = 8
	categories = 20
	probeID    = 0 // customer 0 / product 0: region and category 'probe'
)

// The query both systems answer: revenue per region and category over non-cancelled orders.
const salesQuery = `SELECT c.region, p.category, count(*) AS orders, sum(o.qty * o.price_cents) AS revenue_cents
FROM orders o
JOIN customers c ON c.customer_id = o.customer_id
JOIN products p ON p.product_id = o.product_id
WHERE o.status <> 'cancelled'
GROUP BY c.region, p.category`

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func envInt(k string, d int) int {
	n, err := strconv.Atoi(env(k, strconv.Itoa(d)))
	if err != nil {
		die(err)
	}
	return n
}

func die(err error) {
	fmt.Fprintln(os.Stderr, "error:", err)
	os.Exit(1)
}

func must(_ any, err error) {
	if err != nil {
		die(err)
	}
}

// ---------------------------------------------------------------- latency recorder

type hist struct {
	mu sync.Mutex
	d  []time.Duration
}

func (h *hist) add(d time.Duration) { h.mu.Lock(); h.d = append(h.d, d); h.mu.Unlock() }

func (h *hist) pct(p float64) time.Duration {
	h.mu.Lock()
	defer h.mu.Unlock()
	if len(h.d) == 0 {
		return 0
	}
	s := slices.Clone(h.d)
	slices.Sort(s)
	return s[min(len(s)-1, int(float64(len(s))*p))]
}

func (h *hist) n() int { h.mu.Lock(); defer h.mu.Unlock(); return len(h.d) }

func (h *hist) String() string {
	return fmt.Sprintf("n=%d p50=%s p99=%s max=%s", h.n(), ms(h.pct(0.5)), ms(h.pct(0.99)), ms(h.pct(1)))
}

func ms(d time.Duration) string { return fmt.Sprintf("%.1fms", float64(d.Microseconds())/1000) }

// ---------------------------------------------------------------- setup

func exec(ctx context.Context, db *pgxpool.Pool, sql string) time.Duration {
	start := time.Now()
	if _, err := db.Exec(ctx, sql); err != nil {
		die(fmt.Errorf("%w\n%s", err, sql))
	}
	return time.Since(start)
}

func seed(ctx context.Context, pg *pgxpool.Pool, orders int) {
	start := time.Now()
	exec(ctx, pg, fmt.Sprintf(`INSERT INTO customers SELECT g, 'customer-' || g, 'region-' || (g %% %d) FROM generate_series(1, %d) g`, regions, customers))
	exec(ctx, pg, `INSERT INTO customers VALUES (0, 'probe', 'probe')`)
	exec(ctx, pg, fmt.Sprintf(`INSERT INTO products SELECT g, 'product-' || g, 'category-' || (g %% %d) FROM generate_series(1, %d) g`, categories, products))
	exec(ctx, pg, `INSERT INTO products VALUES (0, 'probe', 'probe')`)
	exec(ctx, pg, fmt.Sprintf(`INSERT INTO orders (order_id, customer_id, product_id, qty, price_cents, status, created_at)
SELECT g, 1 + (g * 7919) %% %d, 1 + (g * 104729) %% %d, 1 + g %% 5, 100 + (g * 31) %% 9900,
       CASE WHEN g %% 10 = 0 THEN 'cancelled' WHEN g %% 3 = 0 THEN 'shipped' ELSE 'paid' END,
       now() - (g || ' seconds')::interval
FROM generate_series(1::bigint, %d) g`, customers, products, orders))
	exec(ctx, pg, `ANALYZE`)
	fmt.Printf("seed: %d customers, %d products, %d orders in Postgres in %.1fs\n", customers+1, products+1, orders, time.Since(start).Seconds())
}

func setupRisingWave(ctx context.Context, rw *pgxpool.Pool, orders int) {
	exec(ctx, rw, `CREATE SOURCE shop_pg WITH (
    connector = 'postgres-cdc',
    hostname = 'postgres', port = '5432',
    username = 'postgres', password = 'postgres',
    database.name = 'shop', schema.name = 'public',
    slot.name = 'rw_shop'
)`)
	start := time.Now()
	exec(ctx, rw, `CREATE TABLE customers (customer_id INT PRIMARY KEY, name VARCHAR, region VARCHAR)
FROM shop_pg TABLE 'public.customers'`)
	exec(ctx, rw, `CREATE TABLE products (product_id INT PRIMARY KEY, name VARCHAR, category VARCHAR)
FROM shop_pg TABLE 'public.products'`)
	exec(ctx, rw, `CREATE TABLE orders (order_id BIGINT PRIMARY KEY, customer_id INT, product_id INT, qty INT,
    price_cents BIGINT, status VARCHAR, created_at TIMESTAMPTZ)
FROM shop_pg TABLE 'public.orders'`)
	// CREATE TABLE ... FROM returns before the snapshot is loaded; wait for all rows.
	for {
		var n int64
		if err := rw.QueryRow(ctx, `SELECT count(*) FROM orders`).Scan(&n); err != nil {
			die(err)
		}
		if n >= int64(orders) {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	snap := time.Since(start)
	fmt.Printf("cdc snapshot: %d orders (+ customers, products) visible in RisingWave after %.1fs (%.0f rows/s)\n",
		orders, snap.Seconds(), float64(orders)/snap.Seconds())

	exec(ctx, rw, `CREATE TABLE page_views (customer_id INT, product_id INT, viewed_at TIMESTAMPTZ) APPEND ONLY`)
	d := exec(ctx, rw, `CREATE MATERIALIZED VIEW sales_by_region_category AS `+salesQuery)
	fmt.Printf("CREATE MATERIALIZED VIEW sales_by_region_category (backfill over %d orders): %.1fs\n", orders, d.Seconds())
	d = exec(ctx, rw, `CREATE MATERIALIZED VIEW product_funnel AS
SELECT p.product_id, p.category, v.views, coalesce(o.orders, 0) AS orders,
       coalesce(o.orders, 0)::double precision / v.views AS conversion
FROM products p
JOIN (SELECT product_id, count(*) AS views FROM page_views GROUP BY product_id) v ON v.product_id = p.product_id
LEFT JOIN (SELECT product_id, count(*) AS orders FROM orders WHERE status <> 'cancelled' GROUP BY product_id) o
       ON o.product_id = p.product_id`)
	fmt.Printf("CREATE MATERIALIZED VIEW product_funnel: %.1fs\n", d.Seconds())
}

// ---------------------------------------------------------------- load

type load struct {
	pg, rw    *pgxpool.Pool
	nextOrder atomic.Int64
	nextProbe atomic.Int64
	events    atomic.Int64 // page views acknowledged by RisingWave
	ops       [5]atomic.Int64
	opLat     hist
	errs      atomic.Int64
	lastErr   atomic.Value
}

var opNames = [5]string{"new order", "ship", "cancel", "change qty", "move customer"}

func (l *load) oltp(ctx context.Context) {
	r := rand.New(rand.NewPCG(rand.Uint64(), rand.Uint64()))
	for ctx.Err() == nil {
		var op int
		var sql string
		var args []any
		maxID := l.nextOrder.Load() - 1
		switch x := r.IntN(1000); {
		case x < 600:
			op, sql = 0, `INSERT INTO orders (order_id, customer_id, product_id, qty, price_cents, status) VALUES ($1, $2, $3, $4, $5, 'paid')`
			args = []any{l.nextOrder.Add(1) - 1, 1 + r.IntN(customers), 1 + r.IntN(products), 1 + r.IntN(5), 100 + r.IntN(9900)}
		case x < 850:
			op, sql = 1, `UPDATE orders SET status = 'shipped' WHERE order_id = $1 AND status = 'paid'`
			args = []any{1 + r.Int64N(maxID)}
		case x < 950:
			op, sql = 2, `UPDATE orders SET status = 'cancelled' WHERE order_id = $1`
			args = []any{1 + r.Int64N(maxID)}
		case x < 999:
			op, sql = 3, `UPDATE orders SET qty = qty % 5 + 1 WHERE order_id = $1`
			args = []any{1 + r.Int64N(maxID)}
		default: // moves ~100 orders between regions in the MV
			op, sql = 4, `UPDATE customers SET region = $2 WHERE customer_id = $1`
			args = []any{1 + r.IntN(customers), fmt.Sprintf("region-%d", r.IntN(regions))}
		}
		start := time.Now()
		if _, err := l.pg.Exec(ctx, sql, args...); err != nil {
			if ctx.Err() == nil {
				l.errs.Add(1)
				l.lastErr.Store(err.Error())
			}
			continue
		}
		l.opLat.add(time.Since(start))
		l.ops[op].Add(1)
	}
}

// page views into RisingWave at `rate` rows/s per worker, 100 rows per INSERT
func (l *load) pageViews(ctx context.Context, rate int) {
	r := rand.New(rand.NewPCG(rand.Uint64(), rand.Uint64()))
	const batch = 100
	tick := time.NewTicker(time.Second * batch / time.Duration(rate))
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
		var sb strings.Builder
		sb.WriteString("INSERT INTO page_views VALUES ")
		for i := range batch {
			if i > 0 {
				sb.WriteByte(',')
			}
			fmt.Fprintf(&sb, "(%d,%d,now())", 1+r.IntN(customers), 1+r.IntN(products))
		}
		if _, err := l.rw.Exec(context.Background(), sb.String()); err != nil {
			l.errs.Add(1)
			l.lastErr.Store(err.Error())
			continue
		}
		l.events.Add(batch)
	}
}

// insertProbe commits one order for the probe customer/product and returns the probe count
// the result must reach and the commit time.
func (l *load) insertProbe(ctx context.Context) (int64, time.Time, error) {
	id := l.nextProbe.Add(1)
	_, err := l.pg.Exec(ctx, `INSERT INTO orders (order_id, customer_id, product_id, qty, price_cents, status)
VALUES ($1, 0, 0, 1, 100, 'paid')`, 1_000_000_000_000+id)
	return id, time.Now(), err
}

func probeCount(ctx context.Context, rw *pgxpool.Pool) (int64, error) {
	var n int64
	err := rw.QueryRow(ctx, `SELECT orders FROM sales_by_region_category WHERE region = 'probe' AND category = 'probe'`).Scan(&n)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, nil
	}
	return n, err
}

// phase 1: freshness of the RisingWave MV. Every second commit a probe order in Postgres, then
// poll the MV every 10 ms until it counts it.
func (l *load) probeMV(ctx context.Context, fresh *hist) {
	for ctx.Err() == nil {
		want, committed, err := l.insertProbe(ctx)
		if err != nil {
			continue
		}
		for ctx.Err() == nil {
			n, err := probeCount(context.Background(), l.rw)
			if err == nil && n >= want {
				fresh.add(time.Since(committed))
				break
			}
			time.Sleep(10 * time.Millisecond)
		}
		sleepCtx(ctx, time.Second)
	}
}

// reads of the MV itself (what a dashboard would do)
func (l *load) readMV(ctx context.Context, lat *hist) {
	for ctx.Err() == nil {
		start := time.Now()
		rows, err := l.rw.Query(ctx, `SELECT region, category, orders, revenue_cents FROM sales_by_region_category`)
		if err == nil {
			for rows.Next() {
			}
			rows.Close()
			err = rows.Err()
		}
		if err == nil {
			lat.add(time.Since(start))
		}
		sleepCtx(ctx, 100*time.Millisecond)
	}
}

// phase 2: the same query re-run on Postgres every `interval` (back to back if the query is
// slower). Freshness of a probe = time from its commit to the end of the first poll whose result
// includes it.
func (l *load) pollPostgres(ctx context.Context, interval time.Duration, qlat, fresh *hist) {
	type seen struct {
		n   int64
		end time.Time
	}
	var mu sync.Mutex
	var last seen
	var wg sync.WaitGroup
	wg.Add(1)
	go func() { // probes
		defer wg.Done()
		for ctx.Err() == nil {
			want, committed, err := l.insertProbe(ctx)
			if err != nil {
				continue
			}
			for ctx.Err() == nil {
				mu.Lock()
				s := last
				mu.Unlock()
				if s.n >= want && s.end.After(committed) {
					fresh.add(s.end.Sub(committed))
					break
				}
				time.Sleep(5 * time.Millisecond)
			}
			sleepCtx(ctx, time.Second)
		}
	}()
	for ctx.Err() == nil {
		start := time.Now()
		rows, err := l.pg.Query(ctx, salesQuery)
		var probe int64
		if err == nil {
			for rows.Next() {
				var region, category, revenue string
				var orders int64
				if err := rows.Scan(&region, &category, &orders, &revenue); err == nil && region == "probe" {
					probe = orders
				}
			}
			rows.Close()
			err = rows.Err()
		}
		if err == nil {
			end := time.Now()
			qlat.add(end.Sub(start))
			mu.Lock()
			last = seen{probe, end}
			mu.Unlock()
		}
		sleepCtx(ctx, interval-time.Since(start))
	}
	wg.Wait()
}

func sleepCtx(ctx context.Context, d time.Duration) {
	if d <= 0 {
		return
	}
	select {
	case <-ctx.Done():
	case <-time.After(d):
	}
}

// runPhase runs OLTP (+ extra goroutines) for dur and prints per-op counts and latency.
func (l *load) runPhase(name string, dur time.Duration, oltpWorkers int, extra ...func(context.Context)) {
	for i := range l.ops {
		l.ops[i].Store(0)
	}
	l.opLat = hist{}
	ctx, cancel := context.WithTimeout(context.Background(), dur)
	defer cancel()
	var wg sync.WaitGroup
	for range oltpWorkers {
		wg.Add(1)
		go func() { defer wg.Done(); l.oltp(ctx) }()
	}
	for _, f := range extra {
		wg.Add(1)
		go func() { defer wg.Done(); f(ctx) }()
	}
	wg.Wait()
	var total int64
	var parts []string
	for i, n := range opNames {
		c := l.ops[i].Load()
		total += c
		parts = append(parts, fmt.Sprintf("%s %d", n, c))
	}
	fmt.Printf("%s: Postgres OLTP %.0f tx/s (%s), latency %s\n", name, float64(total)/dur.Seconds(), strings.Join(parts, ", "), &l.opLat)
}

// ---------------------------------------------------------------- correctness

func salesMap(ctx context.Context, db *pgxpool.Pool, sql string) (map[string]string, error) {
	rows, err := db.Query(ctx, sql)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	m := map[string]string{}
	for rows.Next() {
		var region, category, revenue string
		var orders int64
		if err := rows.Scan(&region, &category, &orders, &revenue); err != nil {
			return nil, err
		}
		m[region+"/"+category] = fmt.Sprintf("%d/%s", orders, revenue)
	}
	return m, rows.Err()
}

func diff(a, b map[string]string) int {
	n := 0
	for k, v := range a {
		if b[k] != v {
			n++
		}
	}
	for k := range b {
		if _, ok := a[k]; !ok {
			n++
		}
	}
	return n
}

func check(ctx context.Context, l *load) bool {
	pgSQL := `SELECT region, category, orders, revenue_cents::text FROM (` + salesQuery + `) q`
	mvSQL := `SELECT region, category, orders, revenue_cents::varchar FROM sales_by_region_category`
	want, err := salesMap(ctx, l.pg, pgSQL)
	if err != nil {
		die(err)
	}
	start := time.Now()
	var got map[string]string
	for time.Since(start) < 120*time.Second {
		if got, err = salesMap(ctx, l.rw, mvSQL); err == nil && diff(want, got) == 0 {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	ok := true
	d := diff(want, got)
	var orders, revenue int64
	for _, v := range want {
		var o, r int64
		fmt.Sscanf(v, "%d/%d", &o, &r)
		orders += o
		revenue += r
	}
	fmt.Printf("check: sales_by_region_category vs the query on Postgres: %d groups, %d orders, revenue %d cents; %d differing groups (converged %.1fs after load stopped)\n",
		len(want), orders, revenue, d, time.Since(start).Seconds())
	if d != 0 {
		ok = false
	}
	var views int64
	if err := l.rw.QueryRow(ctx, `SELECT coalesce(sum(views), 0) FROM product_funnel`).Scan(&views); err != nil {
		die(err)
	}
	var pgOrders, rwOrders int64
	must(nil, l.pg.QueryRow(ctx, `SELECT count(*) FROM orders`).Scan(&pgOrders))
	must(nil, l.rw.QueryRow(ctx, `SELECT count(*) FROM orders`).Scan(&rwOrders))
	fmt.Printf("check: page views acknowledged %d, sum(views) in product_funnel %d; orders rows Postgres %d, RisingWave %d\n",
		l.events.Load(), views, pgOrders, rwOrders)
	if views != l.events.Load() || pgOrders != rwOrders {
		ok = false
	}
	return ok
}

// ---------------------------------------------------------------- main

func main() {
	ctx := context.Background()
	seedOrders := envInt("SEED_ORDERS", 1000000)
	dur := time.Duration(envInt("DURATION", 30)) * time.Second
	oltpWorkers := envInt("OLTP_WORKERS", 8)
	eventWorkers := envInt("EVENT_WORKERS", 2)
	eventRate := envInt("EVENT_RATE", 2500) // rows/s per event worker
	pollEvery := time.Duration(envInt("POLL_INTERVAL", 5)) * time.Second

	pgCfg, err := pgxpool.ParseConfig(env("PG_URL", "postgres://postgres:postgres@postgres:5432/shop?sslmode=disable"))
	if err != nil {
		die(err)
	}
	pgCfg.MaxConns = int32(oltpWorkers + 4)
	pg, err := pgxpool.NewWithConfig(ctx, pgCfg)
	if err != nil {
		die(err)
	}
	rwCfg, err := pgxpool.ParseConfig(env("RW_URL", "postgres://root@risingwave:4566/dev?sslmode=disable"))
	if err != nil {
		die(err)
	}
	rwCfg.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeSimpleProtocol
	rwCfg.MaxConns = int32(eventWorkers + 4)
	rw, err := pgxpool.NewWithConfig(ctx, rwCfg)
	if err != nil {
		die(err)
	}

	var existing int64
	must(nil, pg.QueryRow(ctx, `SELECT count(*) FROM orders`).Scan(&existing))
	if existing != 0 {
		die(errors.New("orders is not empty: run `make down up` first"))
	}
	fmt.Printf("config: SEED_ORDERS=%d DURATION=%s OLTP_WORKERS=%d EVENT_WORKERS=%d x %d rows/s POLL_INTERVAL=%s\n",
		seedOrders, dur, oltpWorkers, eventWorkers, eventRate, pollEvery)
	seed(ctx, pg, seedOrders)
	setupRisingWave(ctx, rw, seedOrders)

	l := &load{pg: pg, rw: rw}
	l.nextOrder.Store(int64(seedOrders) + 1)

	// Query cost on each side with no load: the full query on Postgres vs reading the MV.
	for _, q := range []struct {
		name string
		db   *pgxpool.Pool
		sql  string
	}{
		{"Postgres: the query", pg, salesQuery},
		{"RisingWave: SELECT * FROM the MV", rw, `SELECT * FROM sales_by_region_category`},
	} {
		var h hist
		for range 5 {
			start := time.Now()
			rows, err := q.db.Query(ctx, q.sql)
			if err != nil {
				die(err)
			}
			for rows.Next() {
			}
			rows.Close()
			h.add(time.Since(start))
		}
		fmt.Printf("idle, %s: %s\n", q.name, &h)
	}

	fmt.Println()
	var fresh1, mvRead, fresh2, pgQuery hist
	l.runPhase("phase 1 (RisingWave MVs from CDC)", dur, oltpWorkers, append(
		[]func(context.Context){
			func(c context.Context) { l.probeMV(c, &fresh1) },
			func(c context.Context) { l.readMV(c, &mvRead) },
		},
		repeat(eventWorkers, func(c context.Context) { l.pageViews(c, eventRate) })...)...)
	fmt.Printf("  page views into RisingWave: %d acknowledged (%.0f/s)\n", l.events.Load(), float64(l.events.Load())/dur.Seconds())
	fmt.Printf("  freshness, Postgres commit -> visible in MV: %s\n", &fresh1)
	fmt.Printf("  MV read (SELECT all groups) latency: %s\n", &mvRead)

	l.runPhase(fmt.Sprintf("phase 2 (same query on Postgres every %s)", pollEvery), dur, oltpWorkers,
		func(c context.Context) { l.pollPostgres(c, pollEvery, &pgQuery, &fresh2) })
	fmt.Printf("  query latency on Postgres under load: %s\n", &pgQuery)
	fmt.Printf("  freshness, Postgres commit -> visible in a poll result: %s\n", &fresh2)
	if n := l.errs.Load(); n > 0 {
		fmt.Printf("errors: %d, last: %v\n", n, l.lastErr.Load())
	}

	fmt.Println()
	if !check(ctx, l) {
		fmt.Println("FAILED")
		os.Exit(1)
	}
	fmt.Println("OK")
}

func repeat(n int, f func(context.Context)) []func(context.Context) {
	s := make([]func(context.Context), n)
	for i := range s {
		s[i] = f
	}
	return s
}
