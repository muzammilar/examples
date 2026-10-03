package main

import (
	"context"
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
// rueidis auto-pipelines the concurrent calls over one connection.
func bench(ctx context.Context, c rueidis.Client, n, workers, keys int) {
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

	fmt.Printf("%-10s %9s %7s %7s\n", "op", "ops/s", "p50 ms", "p99 ms")
	run("SET", func(i, key int) error {
		return c.Do(ctx, c.B().Set().Key(skey(key)).Value("value-"+strconv.Itoa(i)).Build()).Error()
	})
	run("GET", func(i, key int) error {
		return c.Do(ctx, c.B().Get().Key(skey(key)).Build()).Error()
	})
	run("add.lua", func(i, key int) error {
		return addScript.Exec(ctx, c, []string{hkey(key)}, []string{"name", "item", "qty", "1"}).Error()
	})
	// Call i of a worker updates its key for the (i/keysPerWorker + 1)-th time,
	// and add.lua created every key at version 1, so that is the expected version.
	run("update.lua", func(i, key int) error {
		version := strconv.Itoa(i/keysPerWorker + 1)
		v, err := updateScript.Exec(ctx, c, []string{hkey(key)}, []string{version, "qty", strconv.Itoa(i)}).AsInt64()
		if err == nil && v == -1 {
			err = fmt.Errorf("%s: version is not %s", hkey(key), version)
		}
		return err
	})
	run("delete.lua", func(i, key int) error {
		return deleteScript.Exec(ctx, c, []string{hkey(key)}, []string{""}).Error()
	})
}
