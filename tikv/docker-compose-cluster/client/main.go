// A tiny TiKV client: RawKV put/get/scan/delete, then TxnKV with an optimistic
// write conflict and a pessimistic lock conflict. Raw and transactional keys use
// separate prefixes: with API V1 the two APIs must never touch the same keys.
package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"strings"
	"time"

	plog "github.com/pingcap/log"
	"github.com/tikv/client-go/v2/config"
	tikverr "github.com/tikv/client-go/v2/error"
	"github.com/tikv/client-go/v2/kv"
	"github.com/tikv/client-go/v2/rawkv"
	"github.com/tikv/client-go/v2/txnkv"
	"github.com/tikv/client-go/v2/txnkv/transaction"
)

func must(err error) {
	if err != nil {
		log.Fatal(err)
	}
}

func main() {
	// client-go logs every PD/TSO connection step at INFO; keep only errors
	logger, props, err := plog.InitLogger(&plog.Config{Level: "error"})
	must(err)
	plog.ReplaceGlobals(logger, props)

	ctx := context.Background()
	pd := strings.Split(getenv("PD_ADDRS", "pd0:2379"), ",")
	if len(os.Args) == 2 && os.Args[1] == "cleanup-bench" {
		cleanupBench(ctx, pd)
		return
	}
	rawKV(ctx, pd)
	txnKV(ctx, pd)
	fmt.Println("\nOK")
}

func rawKV(ctx context.Context, pd []string) {
	fmt.Println("== RawKV")
	c, err := rawkv.NewClient(ctx, pd, config.DefaultConfig().Security)
	must(err)
	defer c.Close()
	fmt.Printf("cluster id: %d\n", c.ClusterID())

	must(c.DeleteRange(ctx, []byte("raw/"), []byte("raw0"))) // idempotent re-runs
	must(c.Put(ctx, []byte("raw/city"), []byte("Berlin")))
	var keys, vals [][]byte
	for i := 1; i <= 5; i++ {
		keys = append(keys, []byte(fmt.Sprintf("raw/user/%03d", i)))
		vals = append(vals, []byte(fmt.Sprintf("user-%d", i)))
	}
	must(c.BatchPut(ctx, keys, vals))
	v, err := c.Get(ctx, []byte("raw/city"))
	must(err)
	fmt.Printf("get raw/city = %s\n", v)

	ks, vs, err := c.Scan(ctx, []byte("raw/user/"), []byte("raw/user0"), 10)
	must(err)
	fmt.Printf("scan raw/user/ -> %d keys\n", len(ks))
	for i := range ks {
		fmt.Printf("  %s = %s\n", ks[i], vs[i])
	}

	must(c.Delete(ctx, []byte("raw/city")))
	v, err = c.Get(ctx, []byte("raw/city"))
	must(err)
	fmt.Printf("after delete: get raw/city = %v (nil: %t)\n", v, v == nil)

	must(c.DeleteRange(ctx, []byte("raw/user/"), []byte("raw/user0")))
	ks, _, err = c.Scan(ctx, []byte("raw/"), []byte("raw0"), 10)
	must(err)
	fmt.Printf("after delete range: scan raw/ -> %d keys\n", len(ks))
}

func txnKV(ctx context.Context, pd []string) {
	fmt.Println("\n== TxnKV")
	c, err := txnkv.NewClient(pd)
	must(err)
	defer c.Close()

	// One transaction writes three keys atomically (Percolator 2PC across regions).
	t, err := c.Begin()
	must(err)
	for _, k := range []string{"txn/alice", "txn/bob", "txn/carol"} {
		must(t.Set([]byte(k), []byte("100")))
	}
	must(t.Commit(ctx))
	fmt.Printf("committed 3 keys in one transaction (start_ts=%d)\n", t.StartTS())

	t, err = c.Begin()
	must(err)
	it, err := t.Iter([]byte("txn/"), []byte("txn0"))
	must(err)
	for it.Valid() {
		fmt.Printf("  %s = %s\n", it.Key(), it.Value())
		must(it.Next())
	}
	it.Close()
	must(t.Rollback())

	// Optimistic: t1 and t2 both write txn/alice; the first to commit wins, the
	// second fails its prewrite with a write conflict.
	fmt.Println("-- optimistic conflict")
	t1, err := c.Begin()
	must(err)
	t2, err := c.Begin()
	must(err)
	must(t1.Set([]byte("txn/alice"), []byte("90")))
	must(t2.Set([]byte("txn/alice"), []byte("80")))
	must(t1.Commit(ctx))
	fmt.Println("t1 commit: ok")
	err = t2.Commit(ctx)
	fmt.Printf("t2 commit: write conflict = %t\n", tikverr.IsErrWriteConflict(err))
	if err == nil {
		log.Fatal("expected a write conflict")
	}

	// Pessimistic: t3 locks txn/bob; t4 tries the same lock without waiting and
	// fails immediately; after t3 commits, t4's retry succeeds.
	fmt.Println("-- pessimistic lock")
	t3 := pessimistic(c)
	must(t3.LockKeys(ctx, lockCtx(ctx, c), []byte("txn/bob")))
	fmt.Println("t3 locked txn/bob")
	t4 := pessimistic(c)
	err = t4.LockKeys(ctx, lockCtx(ctx, c), []byte("txn/bob"))
	fmt.Printf("t4 lock (nowait) while t3 holds it: %v\n", errText(err))
	if err == nil {
		log.Fatal("expected a lock conflict")
	}
	must(t4.Rollback())
	must(t3.Set([]byte("txn/bob"), []byte("110")))
	must(t3.Commit(ctx))
	fmt.Println("t3 commit: ok")
	t4 = pessimistic(c)
	must(t4.LockKeys(ctx, lockCtx(ctx, c), []byte("txn/bob")))
	v, err := t4.Get(ctx, []byte("txn/bob"))
	must(err)
	must(t4.Set([]byte("txn/bob"), []byte("120")))
	must(t4.Commit(ctx))
	fmt.Printf("t4 retry: read txn/bob=%s, wrote 120, commit ok\n", v.Value)

	t, err = c.Begin()
	must(err)
	for _, k := range []string{"txn/alice", "txn/bob", "txn/carol"} {
		v, err := t.Get(ctx, []byte(k))
		must(err)
		fmt.Printf("  %s = %s\n", k, v.Value)
	}
	must(t.Rollback())
}

// cleanupBench removes the rows `make benchmark` (go-ycsb) wrote: table ycsb_raw through
// RawKV (keys "ycsb_raw:<key>"), table ycsb_txn through TxnKV, 1000 deletes per transaction.
func cleanupBench(ctx context.Context, pd []string) {
	r, err := rawkv.NewClient(ctx, pd, config.DefaultConfig().Security)
	must(err)
	defer r.Close()
	must(r.DeleteRange(ctx, []byte("ycsb_raw:"), []byte("ycsb_raw;")))
	fmt.Println("cleanup: deleted RawKV range ycsb_raw:")

	// Once the RawKV load has split regions at raw (unencoded) keys, the TxnKV client can no
	// longer iterate ycsb_txn: ("failed to decode region range key"), so drop the txn keys'
	// MVCC records directly: the memcomparable-encoded range in each column family.
	from, to := encodeBytes([]byte("ycsb_txn:")), encodeBytes([]byte("ycsb_txn;"))
	for _, cf := range []string{"default", "lock", "write"} {
		must(r.DeleteRange(ctx, from, to, rawkv.SetColumnFamily(cf)))
	}
	fmt.Println("cleanup: deleted TxnKV range ycsb_txn: (default, lock and write column families)")
}

// encodeBytes is TiKV's memcomparable key encoding (what TxnKV stores keys as): 8-byte
// groups, zero-padded, each followed by 0xFF minus the number of padding bytes.
func encodeBytes(b []byte) []byte {
	out := make([]byte, 0, (len(b)/8+1)*9)
	for i := 0; i <= len(b); i += 8 {
		g := make([]byte, 8)
		n := copy(g, b[i:])
		out = append(append(out, g...), byte(0xFF-(8-n)))
	}
	return out
}

func pessimistic(c *txnkv.Client) *transaction.KVTxn {
	t, err := c.Begin()
	must(err)
	t.SetPessimistic(true)
	return t
}

func lockCtx(ctx context.Context, c *txnkv.Client) *kv.LockCtx {
	ts, err := c.GetTimestamp(ctx)
	must(err)
	return kv.NewLockCtx(ts, kv.LockNoWait, time.Now())
}

func errText(err error) string {
	if err == nil {
		return "<nil>"
	}
	s := err.Error()
	if len(s) > 120 {
		s = s[:120] + "..."
	}
	return s
}

func getenv(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
