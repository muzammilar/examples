package main

import (
	"context"
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"log"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/redis/rueidis"
)

// bench runs n operations of each command from c workers. Worker w only touches
// its own slice of the key space, so it knows the version of every hash it owns
// and update.lua can do a real CAS without reading the version first.
// rueidis auto-pipelines the concurrent calls over one connection (with wait > 0
// only reads; see write).
func bench(ctx context.Context, c rueidis.Client, n, workers, keys, wait int) {
	keysPerWorker := keys / workers
	if keysPerWorker == 0 {
		log.Fatal("-keys must be at least -c")
	}
	opsPerWorker := n / workers
	fmt.Printf("%d ops per command, %d workers, %d keys\n", opsPerWorker*workers, workers, keysPerWorker*workers)

	skey := func(k int) string { return "bench:s:" + strconv.Itoa(k) }
	hkey := func(k int) string { return "bench:h:" + strconv.Itoa(k) }

	// Clear keys from an earlier run so add.lua starts from nothing. One DEL per key,
	// so on a cluster each one goes to the node that owns its slot.
	for k := 0; k < keys; k += 1000 {
		cmds := make(rueidis.Commands, 0, 2000)
		for i := k; i < min(k+1000, keys); i++ {
			cmds = append(cmds, c.B().Del().Key(skey(i)).Build(), c.B().Del().Key(hkey(i)).Build())
		}
		for _, r := range c.DoMulti(ctx, cmds...) {
			if err := r.Error(); err != nil {
				log.Fatal(err)
			}
		}
	}

	// run calls op opsPerWorker times in each worker and prints ops/s, p50 and p99.
	// op gets the call number i and the key to use; worker w cycles through keys
	// w*keysPerWorker up to (w+1)*keysPerWorker - 1.
	run := func(name string, op func(i, key int) error) {
		latencies := make([][]time.Duration, workers)
		var wg sync.WaitGroup
		began := time.Now()
		for w := range workers {
			firstKey := w * keysPerWorker
			wg.Go(func() {
				took := make([]time.Duration, opsPerWorker)
				for i := range took {
					t := time.Now()
					if err := op(i, firstKey+i%keysPerWorker); err != nil {
						log.Fatalf("%s: %v", name, err)
					}
					took[i] = time.Since(t)
				}
				latencies[w] = took
			})
		}
		wg.Wait()
		elapsed := time.Since(began)

		all := slices.Concat(latencies...)
		slices.Sort(all)
		ms := func(q float64) float64 { return all[int(q*float64(len(all)-1))].Seconds() * 1000 }
		fmt.Printf("%-10s %9.0f %7.2f %7.2f\n", name, float64(len(all))/elapsed.Seconds(), ms(0.50), ms(0.99))
	}

	// write sends a write command and returns its reply. With -wait the command is followed by
	// WAIT <wait> 0 in the same round trip. WAIT blocks its connection, so rueidis sends the
	// pair on a dedicated connection (one per worker) instead of auto-pipelining it over the
	// shared one; WAIT covers the writes of its own connection.
	write := func(cmd rueidis.Completed) (rueidis.RedisResult, error) {
		if wait == 0 {
			r := c.Do(ctx, cmd)
			return r, r.Error()
		}
		res := c.DoMulti(ctx, cmd, c.B().Wait().Numreplicas(int64(wait)).Timeout(0).Build())
		if err := res[0].Error(); err != nil {
			return res[0], err
		}
		acked, err := res[1].AsInt64()
		if err == nil && acked < int64(wait) {
			err = fmt.Errorf("WAIT: %d of %d replicas acknowledged", acked, wait)
		}
		return res[0], err
	}
	// script runs a Lua script on one key: through rueidis (EVALSHA, loading the script on
	// NOSCRIPT) or, with -wait, as an EVALSHA built here and sent by write (loaded below).
	script := func(s *rueidis.Lua, src, key string, args ...string) (int64, error) {
		if wait == 0 {
			return s.Exec(ctx, c, []string{key}, args).AsInt64()
		}
		sum := sha1.Sum([]byte(src))
		r, err := write(c.B().Evalsha().Sha1(hex.EncodeToString(sum[:])).Numkeys(1).Key(key).Arg(args...).Build())
		if err != nil {
			return 0, err
		}
		return r.AsInt64()
	}
	if wait > 0 {
		for _, src := range []string{addSrc, updateSrc, deleteSrc} {
			if err := c.Do(ctx, c.B().ScriptLoad().Script(src).Build()).Error(); err != nil {
				log.Fatalf("SCRIPT LOAD: %v", err)
			}
		}
		fmt.Printf("WAIT %d 0 after every write\n", wait)
	}

	fmt.Printf("%-10s %9s %7s %7s\n", "op", "ops/s", "p50 ms", "p99 ms")
	run("SET", func(i, key int) error {
		_, err := write(c.B().Set().Key(skey(key)).Value("value-" + strconv.Itoa(i)).Build())
		return err
	})
	run("GET", func(i, key int) error {
		return c.Do(ctx, c.B().Get().Key(skey(key)).Build()).Error()
	})
	run("add.lua", func(i, key int) error {
		_, err := script(addScript, addSrc, hkey(key), "name", "item", "qty", "1")
		return err
	})
	// Call i of a worker updates its key for the (i/keysPerWorker + 1)-th time,
	// and add.lua created every key at version 1, so that is the expected version.
	run("update.lua", func(i, key int) error {
		version := strconv.Itoa(i/keysPerWorker + 1)
		v, err := script(updateScript, updateSrc, hkey(key), version, "qty", strconv.Itoa(i))
		if err == nil && v == -1 {
			err = fmt.Errorf("%s: version is not %s", hkey(key), version)
		}
		return err
	})
	run("delete.lua", func(i, key int) error {
		_, err := script(deleteScript, deleteSrc, hkey(key), "")
		return err
	})
}
