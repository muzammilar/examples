package main

import (
	"fmt"
	"log"
	"slices"
	"strconv"
	"sync"
	"time"

	"github.com/tarantool/go-tarantool/v3"
)

// bench runs n operations of each command from c workers, like go/rueidis-lua-bench: worker w
// only touches its own slice of the key space, so it knows the version of every hash it owns
// and bench_update does a real CAS without reading the version first. go-tarantool multiplexes
// the concurrent requests over one connection (as rueidis auto-pipelines).
func bench(conn *tarantool.Connection, n, workers, keys int) {
	keysPerWorker := keys / workers
	if keysPerWorker == 0 {
		log.Fatal("-keys must be at least -c")
	}
	opsPerWorker := n / workers
	fmt.Printf("%d ops per command, %d workers, %d keys\n", opsPerWorker*workers, workers, keysPerWorker*workers)

	skey := func(k int) string { return "bench:s:" + strconv.Itoa(k) }
	hkey := func(k int) string { return "bench:h:" + strconv.Itoa(k) }
	do := func(r tarantool.Request) ([]any, error) { return conn.Do(r).Get() }
	call := func(fn string, args ...any) (int64, error) {
		res, err := do(tarantool.NewCallRequest(fn).Args(args))
		if err != nil {
			return 0, err
		}
		return toInt(res[0]), nil
	}

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

	fields := map[string]any{"name": "item", "qty": "1"}
	fmt.Printf("%-10s %9s %7s %7s\n", "op", "ops/s", "p50 ms", "p99 ms")
	run("put", func(i, key int) error {
		_, err := do(tarantool.NewReplaceRequest("bench_s").Tuple([]any{skey(key), "value-" + strconv.Itoa(i)}))
		return err
	})
	run("get", func(i, key int) error {
		_, err := do(tarantool.NewSelectRequest("bench_s").Key([]any{skey(key)}).Limit(1))
		return err
	})
	run("add", func(i, key int) error {
		_, err := call("bench_add", hkey(key), fields)
		return err
	})
	// Call i of a worker updates its key for the (i/keysPerWorker + 1)-th time, and bench_add
	// created every key at version 1, so that is the expected version.
	run("update", func(i, key int) error {
		version := strconv.Itoa(i/keysPerWorker + 1)
		v, err := call("bench_update", hkey(key), version, map[string]any{"qty": strconv.Itoa(i)})
		if err == nil && v == -1 {
			err = fmt.Errorf("%s: version is not %s", hkey(key), version)
		}
		return err
	})
	run("delete", func(i, key int) error {
		_, err := call("bench_delete", []any{hkey(key)}, "")
		return err
	})
}

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
	}
	log.Fatalf("not an integer: %T %v", v, v)
	return 0
}
