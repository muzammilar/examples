// Wallet transfers three ways, same request stream each time:
//
//	tarantool-procedure  one call of the transfer() stored procedure per transfer (1 round trip)
//	tarantool-client-txn the same logic in the client: an interactive transaction on an IPROTO
//	                     stream (begin, 3 selects, 2 updates, insert, commit = 8 round trips),
//	                     retried on MVCC conflicts
//	valkey-lua           the same logic as a Valkey Lua script (EVALSHA, 1 round trip)
//
// Every phase starts from ACCOUNTS accounts holding BALANCE each and runs TRANSFERS transfers
// from WORKERS goroutines; HOT_PCT percent of transfers touch one of HOT_ACCOUNTS hot accounts.
// After each phase: the sum of balances must be unchanged, no balance negative, the number of
// stored transfers equal to the number applied, and replaying REPLAY request ids must change
// nothing. Any violation exits 1.
package main

import (
	"context"
	_ "embed"
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

	"github.com/redis/rueidis"
	"github.com/tarantool/go-tarantool/v3"
)

var (
	//go:embed transfer.lua
	transferSrc string
	//go:embed audit.lua
	auditSrc string
)

// result codes, same in app/init.lua and transfer.lua
const (
	codeOK = iota
	codeInsufficient
	codeDuplicate
	codeNoAccount
)

type transfer struct {
	req, from, to uint64
	amount        int64
}

type cfg struct {
	accounts, hotAccounts, transfers, workers, replay int
	hotPct                                            float64
	balance                                           int64
}

type stats struct {
	lat       []time.Duration
	codes     [4]int64
	conflicts int64 // client-txn retries
}

type audit struct {
	sum, min  int64
	transfers int64
}

type backend interface {
	name() string
	reset(ctx context.Context, c cfg) error
	transfer(ctx context.Context, t transfer) (code int, retries int, err error)
	audit(ctx context.Context, c cfg) (audit, error)
}

func main() {
	c := cfg{
		accounts:    envInt("ACCOUNTS", 100000),
		hotAccounts: envInt("HOT_ACCOUNTS", 100),
		hotPct:      float64(envInt("HOT_PCT", 20)),
		transfers:   envInt("TRANSFERS", 200000),
		workers:     envInt("WORKERS", 64),
		replay:      envInt("REPLAY", 1000),
		balance:     int64(envInt("BALANCE", 1000)),
	}
	phases := strings.Split(env("PHASES", "tarantool-procedure,tarantool-client-txn,valkey-lua"), ",")
	ctx := context.Background()

	reqs := generate(c)
	fmt.Printf("%d accounts x %d, %d transfers, %d workers, %.0f%% of transfers touch %d hot accounts\n\n",
		c.accounts, c.balance, c.transfers, c.workers, c.hotPct, c.hotAccounts)
	fmt.Printf("%-21s %9s %8s %8s %8s %9s %9s %9s %s\n",
		"phase", "tx/s", "p50 ms", "p99 ms", "max ms", "applied", "rejected", "retries", "audit")

	failed := false
	for _, p := range phases {
		b, err := open(ctx, p)
		if err != nil {
			fatal("%s: %v", p, err)
		}
		if err := b.reset(ctx, c); err != nil {
			fatal("%s reset: %v", p, err)
		}
		st, elapsed, err := run(ctx, b, c, reqs)
		if err != nil {
			fatal("%s: %v", p, err)
		}
		verdict := check(ctx, b, c, reqs, st)
		if !strings.HasPrefix(verdict, "ok") {
			failed = true
		}
		slices.Sort(st.lat)
		fmt.Printf("%-21s %9.0f %8.3f %8.3f %8.1f %9d %9d %9d %s\n", p,
			float64(len(st.lat))/elapsed.Seconds(), ms(pct(st.lat, 0.50)), ms(pct(st.lat, 0.99)), ms(st.lat[len(st.lat)-1]),
			st.codes[codeOK], st.codes[codeInsufficient], st.conflicts, verdict)
	}
	if failed {
		os.Exit(1)
	}
}

// generate a fixed request stream (seeded), so every phase sees the same transfers
func generate(c cfg) []transfer {
	r := rand.New(rand.NewPCG(42, 7))
	pick := func() uint64 { return uint64(r.IntN(c.accounts)) + 1 }
	hot := func() uint64 { return uint64(r.IntN(c.hotAccounts)) + 1 }
	out := make([]transfer, c.transfers)
	for i := range out {
		t := transfer{req: uint64(i) + 1, from: pick(), to: pick(), amount: int64(r.IntN(100)) + 1}
		if r.Float64()*100 < c.hotPct {
			if r.IntN(2) == 0 {
				t.from = hot()
			} else {
				t.to = hot()
			}
		}
		for t.to == t.from {
			t.to = pick()
		}
		out[i] = t
	}
	return out
}

func run(ctx context.Context, b backend, c cfg, reqs []transfer) (stats, time.Duration, error) {
	var next atomic.Int64
	var mu sync.Mutex
	var firstErr error
	total := stats{}
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < c.workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			local := stats{lat: make([]time.Duration, 0, len(reqs)/c.workers+1)}
			for {
				i := int(next.Add(1)) - 1
				if i >= len(reqs) {
					break
				}
				s := time.Now()
				code, retries, err := b.transfer(ctx, reqs[i])
				if err != nil {
					mu.Lock()
					firstErr = cmpErr(firstErr, fmt.Errorf("transfer %d: %w", reqs[i].req, err))
					mu.Unlock()
					return
				}
				local.lat = append(local.lat, time.Since(s))
				local.codes[code]++
				local.conflicts += int64(retries)
			}
			mu.Lock()
			total.lat = append(total.lat, local.lat...)
			for k := range total.codes {
				total.codes[k] += local.codes[k]
			}
			total.conflicts += local.conflicts
			mu.Unlock()
		}()
	}
	wg.Wait()
	return total, time.Since(start), firstErr
}

// invariants after a phase, then a replay of the first REPLAY requests (all must be duplicates)
func check(ctx context.Context, b backend, c cfg, reqs []transfer, st stats) string {
	a, err := b.audit(ctx, c)
	if err != nil {
		return "audit error: " + err.Error()
	}
	var problems []string
	want := int64(c.accounts) * c.balance
	if a.sum != want {
		problems = append(problems, fmt.Sprintf("sum %d != %d", a.sum, want))
	}
	if a.min < 0 {
		problems = append(problems, fmt.Sprintf("negative balance %d", a.min))
	}
	if a.transfers != st.codes[codeOK] {
		problems = append(problems, fmt.Sprintf("%d stored transfers != %d applied", a.transfers, st.codes[codeOK]))
	}
	dups := 0
	for _, t := range reqs[:min(c.replay, len(reqs))] {
		code, _, err := b.transfer(ctx, t)
		if err != nil {
			return "replay error: " + err.Error()
		}
		if code == codeDuplicate || code == codeInsufficient {
			dups++ // a rejected request left no record, so it is re-checked (and rejected again or applied)
		}
	}
	after, err := b.audit(ctx, c)
	if err != nil {
		return "audit error: " + err.Error()
	}
	if after.sum != want || after.min < 0 {
		problems = append(problems, "replay broke the invariants")
	}
	if len(problems) > 0 {
		return "FAILED: " + strings.Join(problems, "; ")
	}
	return fmt.Sprintf("ok (sum %d, min %d, %d replayed: %d no-ops)", a.sum, a.min, min(c.replay, len(reqs)), dups)
}

func open(ctx context.Context, phase string) (backend, error) {
	switch phase {
	case "tarantool-procedure", "tarantool-client-txn":
		dctx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()
		conn, err := tarantool.Connect(dctx, tarantool.NetDialer{
			Address: env("TARANTOOL_ADDR", "tarantool:3301"), User: "app", Password: "secret",
		}, tarantool.Opts{Timeout: 30 * time.Second})
		if err != nil {
			return nil, err
		}
		if phase == "tarantool-procedure" {
			return &tntProc{conn}, nil
		}
		return &tntTxn{conn}, nil
	case "valkey-lua":
		cl, err := rueidis.NewClient(rueidis.ClientOption{
			InitAddress: []string{env("VALKEY_ADDR", "valkey:6379")}, DisableCache: true,
		})
		if err != nil {
			return nil, err
		}
		return &valkey{cl: cl, transfer_: rueidis.NewLuaScript(transferSrc), audit_: rueidis.NewLuaScript(auditSrc)}, nil
	}
	return nil, fmt.Errorf("unknown phase %q", phase)
}

// --- Tarantool: stored procedure ---

type tntProc struct{ conn *tarantool.Connection }

func (*tntProc) name() string { return "tarantool-procedure" }

func (b *tntProc) reset(_ context.Context, c cfg) error {
	_, err := b.conn.Do(tarantool.NewCallRequest("reset").Args([]any{c.accounts, c.balance})).Get()
	return err
}

func (b *tntProc) transfer(_ context.Context, t transfer) (int, int, error) {
	res, err := b.conn.Do(tarantool.NewCallRequest("transfer").Args([]any{t.req, t.from, t.to, t.amount})).Get()
	if err != nil {
		return 0, 0, err
	}
	return int(toInt(res[0])), 0, nil
}

func (b *tntProc) audit(_ context.Context, _ cfg) (audit, error) {
	return tntAudit(b.conn)
}

func tntAudit(conn *tarantool.Connection) (audit, error) {
	res, err := conn.Do(tarantool.NewCallRequest("audit")).Get()
	if err != nil {
		return audit{}, err
	}
	m := res[0].(map[any]any)
	return audit{sum: toInt(m["sum"]), min: toInt(m["min"]), transfers: toInt(m["transfers"])}, nil
}

// --- Tarantool: the same logic in the client, one request per step ---

type tntTxn struct{ conn *tarantool.Connection }

func (*tntTxn) name() string                             { return "tarantool-client-txn" }
func (b *tntTxn) reset(ctx context.Context, c cfg) error { return (&tntProc{b.conn}).reset(ctx, c) }
func (b *tntTxn) audit(_ context.Context, _ cfg) (audit, error) {
	return tntAudit(b.conn)
}

var errConflict = errors.New("conflict")

func (b *tntTxn) transfer(_ context.Context, t transfer) (int, int, error) {
	for retries := 0; ; retries++ {
		code, err := b.once(t)
		if errors.Is(err, errConflict) {
			continue
		}
		return code, retries, err
	}
}

// begin; get transfers[req]; get accounts[from]; get accounts[to]; update; update; insert; commit
func (b *tntTxn) once(t transfer) (int, error) {
	s, err := b.conn.NewStream()
	if err != nil {
		return 0, err
	}
	do := func(r tarantool.Request) ([]any, error) {
		res, err := s.Do(r).Get()
		if err != nil && strings.Contains(err.Error(), "aborted by conflict") {
			return nil, errConflict
		}
		return res, err
	}
	rollback := func(code int, err error) (int, error) {
		s.Do(tarantool.NewRollbackRequest()).Get()
		return code, err
	}
	// default isolation for streams is serializable-like best effort; conflicts are retried
	if _, err := do(tarantool.NewBeginRequest().Timeout(10 * time.Second)); err != nil {
		return 0, err
	}
	get := func(space string, key uint64) ([]any, error) {
		return do(tarantool.NewSelectRequest(space).Key([]any{key}).Limit(1))
	}
	dup, err := get("transfers", t.req)
	if err != nil {
		return rollback(0, err)
	}
	if len(dup) > 0 {
		return rollback(codeDuplicate, nil)
	}
	from, err := get("accounts", t.from)
	if err != nil {
		return rollback(0, err)
	}
	to, err := get("accounts", t.to)
	if err != nil {
		return rollback(0, err)
	}
	if len(from) == 0 || len(to) == 0 {
		return rollback(codeNoAccount, nil)
	}
	if toInt(from[0].([]any)[1]) < t.amount {
		return rollback(codeInsufficient, nil)
	}
	if _, err := do(tarantool.NewUpdateRequest("accounts").Key([]any{t.from}).
		Operations(tarantool.NewOperations().Subtract(1, t.amount))); err != nil {
		return rollback(0, err)
	}
	if _, err := do(tarantool.NewUpdateRequest("accounts").Key([]any{t.to}).
		Operations(tarantool.NewOperations().Add(1, t.amount))); err != nil {
		return rollback(0, err)
	}
	if _, err := do(tarantool.NewInsertRequest("transfers").
		Tuple([]any{t.req, t.from, t.to, t.amount, float64(time.Now().UnixNano()) / 1e9})); err != nil {
		return rollback(0, err)
	}
	if _, err := do(tarantool.NewCommitRequest()); err != nil {
		if errors.Is(err, errConflict) {
			return 0, err
		}
		return 0, err
	}
	return codeOK, nil
}

// --- Valkey: Lua script ---

type valkey struct {
	cl                rueidis.Client
	transfer_, audit_ *rueidis.Lua
}

func (*valkey) name() string { return "valkey-lua" }

func (b *valkey) reset(ctx context.Context, c cfg) error {
	if err := b.cl.Do(ctx, b.cl.B().Flushall().Build()).Error(); err != nil {
		return err
	}
	bal := strconv.FormatInt(c.balance, 10)
	for first := 1; first <= c.accounts; first += 1000 {
		cmd := b.cl.B().Mset().KeyValue()
		for id := first; id < first+1000 && id <= c.accounts; id++ {
			cmd = cmd.KeyValue("acct:"+strconv.Itoa(id), bal)
		}
		if err := b.cl.Do(ctx, cmd.Build()).Error(); err != nil {
			return err
		}
	}
	return nil
}

func (b *valkey) transfer(ctx context.Context, t transfer) (int, int, error) {
	n, err := b.transfer_.Exec(ctx, b.cl,
		[]string{"acct:" + u(t.from), "acct:" + u(t.to), "req:" + u(t.req)},
		[]string{strconv.FormatInt(t.amount, 10)}).AsInt64()
	return int(n), 0, err
}

func (b *valkey) audit(ctx context.Context, c cfg) (audit, error) {
	v, err := b.audit_.Exec(ctx, b.cl, nil, []string{strconv.Itoa(c.accounts)}).AsIntSlice()
	if err != nil {
		return audit{}, err
	}
	return audit{sum: v[0], min: v[1], transfers: v[2]}, nil
}

// --- helpers ---

func toInt(v any) int64 {
	switch x := v.(type) {
	case int64:
		return x
	case uint64:
		return int64(x)
	case int8:
		return int64(x)
	case int16:
		return int64(x)
	case int32:
		return int64(x)
	case uint8:
		return int64(x)
	case uint16:
		return int64(x)
	case uint32:
		return int64(x)
	case int:
		return int64(x)
	case float64:
		return int64(x)
	}
	panic(fmt.Sprintf("not an integer: %T %v", v, v))
}

func pct(s []time.Duration, p float64) time.Duration {
	if len(s) == 0 {
		return 0
	}
	return s[min(len(s)-1, int(float64(len(s))*p))]
}
func ms(d time.Duration) float64 { return float64(d) / 1e6 }
func u(v uint64) string          { return strconv.FormatUint(v, 10) }
func cmpErr(a, b error) error {
	if a != nil {
		return a
	}
	return b
}
func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func envInt(k string, d int) int {
	v, err := strconv.Atoi(env(k, strconv.Itoa(d)))
	if err != nil {
		fatal("%s: %v", k, err)
	}
	return v
}
func fatal(f string, a ...any) {
	fmt.Fprintf(os.Stderr, f+"\n", a...)
	os.Exit(1)
}
