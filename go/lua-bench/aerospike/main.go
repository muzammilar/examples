// lua-bench/aerospike runs the go/rueidis-lua-bench workload against Aerospike:
// put and get, then add, update and delete of a versioned record twice, once as
// Lua record UDFs (lua/bench.lua, called with Execute) and once with native
// operations (create-only put, generation-checked operate, delete). It prints
// ops/s, p50 and p99 for each. The client finds every node of a cluster from
// -addr and sends each key to the node that masters its partition.
package main

import (
	_ "embed"
	"flag"
	"fmt"
	"log"
	"net"
	"slices"
	"strconv"
	"sync"
	"time"

	as "github.com/aerospike/aerospike-client-go/v8"
	"github.com/aerospike/aerospike-client-go/v8/types"
)

//go:embed lua/bench.lua
var udfSrc []byte

const (
	namespace = "test"
	udfModule = "bench" // registered as bench.lua
)

func main() {
	addr := flag.String("addr", "127.0.0.1:3000", "server address (host:port); any node of a cluster")
	n := flag.Int("n", 200000, "operations per command")
	workers := flag.Int("c", 50, "concurrent workers")
	keys := flag.Int("keys", 100000, "number of distinct keys")
	flag.Parse()

	host, portStr, err := net.SplitHostPort(*addr)
	if err != nil {
		log.Fatalf("-addr %s: %v", *addr, err)
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		log.Fatalf("-addr %s: %v", *addr, err)
	}
	cp := as.NewClientPolicy()
	cp.ConnectionQueueSize = max(cp.ConnectionQueueSize, *workers)
	c, aerr := as.NewClientWithPolicyAndHost(cp, as.NewHost(host, port))
	if aerr != nil {
		log.Fatalf("connect %s: %v", *addr, aerr)
	}
	defer c.Close()

	task, aerr := c.RegisterUDF(nil, udfSrc, udfModule+".lua", as.LUA)
	if aerr != nil {
		log.Fatalf("register udf: %v", aerr)
	}
	if aerr := <-task.OnComplete(); aerr != nil {
		log.Fatalf("register udf: %v", aerr)
	}

	fmt.Printf("%s: %d nodes, udf %s.lua registered\n", *addr, len(c.GetNodes()), udfModule)
	bench(c, *n, *workers, *keys)
}

// bench runs n operations of each command from c workers. Worker w only touches
// its own slice of the key space, so it knows the version of every record it
// owns and the updates can check it without reading the record first.
func bench(c *as.Client, n, workers, keys int) {
	keysPerWorker := keys / workers
	if keysPerWorker == 0 {
		log.Fatal("-keys must be at least -c")
	}
	opsPerWorker := n / workers
	fmt.Printf("%d ops per command, %d workers, %d keys\n", opsPerWorker*workers, workers, keysPerWorker*workers)

	// set kv for put/get, set udf for the UDF records, set native for the native ones
	key := func(set string, k int) *as.Key {
		key, err := as.NewKey(namespace, set, "bench:"+strconv.Itoa(k))
		if err != nil {
			log.Fatal(err)
		}
		return key
	}

	// Delete records from an earlier run so add starts from nothing.
	for _, set := range []string{"kv", "udf", "native"} {
		for k := 0; k < keys; k += 1000 {
			batch := make([]*as.Key, 0, 1000)
			for i := k; i < min(k+1000, keys); i++ {
				batch = append(batch, key(set, i))
			}
			if _, err := c.BatchDelete(nil, nil, batch); err != nil {
				log.Fatalf("clear %s: %v", set, err)
			}
		}
	}

	wp := as.NewWritePolicy(0, 0)
	wp.TotalTimeout = 10 * time.Second
	rp := as.NewPolicy()
	rp.TotalTimeout = 10 * time.Second

	// run calls op opsPerWorker times in each worker and prints ops/s, p50 and p99.
	// op gets the call number i and the key to use; worker w cycles through keys
	// w*keysPerWorker up to (w+1)*keysPerWorker - 1.
	run := func(name string, op func(i, k int) error) {
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
		fmt.Printf("%-14s %9.0f %7.2f %7.2f\n", name, float64(len(all))/elapsed.Seconds(), ms(0.50), ms(0.99))
	}
	asErr := func(err as.Error) error {
		if err == nil {
			return nil
		}
		return err
	}
	// Call i of a worker touches its key for the (i/keysPerWorker + 1)-th time,
	// and add created every key at version 1, so that is the expected version.
	expected := func(i int) int { return i/keysPerWorker + 1 }
	fields := func(i int) as.Value { return as.NewValue(map[string]any{"qty": i}) }

	fmt.Printf("%-14s %9s %7s %7s\n", "op", "ops/s", "p50 ms", "p99 ms")
	run("put", func(i, k int) error {
		return asErr(c.PutBins(wp, key("kv", k), as.NewBin("value", "value-"+strconv.Itoa(i))))
	})
	run("get", func(i, k int) error {
		_, err := c.Get(rp, key("kv", k))
		return asErr(err)
	})

	// Lua record UDFs, one Execute per call
	addFields := as.NewValue(map[string]any{"name": "item", "qty": 1})
	run("add udf", func(i, k int) error {
		_, err := c.Execute(wp, key("udf", k), udfModule, "add", addFields)
		return asErr(err)
	})
	run("update udf", func(i, k int) error {
		v, err := c.Execute(wp, key("udf", k), udfModule, "update", as.NewValue(expected(i)), fields(i))
		if err != nil {
			return err
		}
		if v != expected(i)+1 {
			return fmt.Errorf("key %d: update returned %v, want version %d", k, v, expected(i)+1)
		}
		return nil
	})
	run("delete udf", func(i, k int) error {
		_, err := c.Execute(wp, key("udf", k), udfModule, "delete", as.NewNullValue())
		return asErr(err)
	})

	// Native operations: the record generation is the version (1 on create, +1 per write).
	create := as.NewWritePolicy(0, 0)
	create.TotalTimeout = wp.TotalTimeout
	create.RecordExistsAction = as.CREATE_ONLY
	run("add native", func(i, k int) error {
		err := c.PutBins(create, key("native", k), as.NewBin("version", 1), as.NewBin("name", "item"), as.NewBin("qty", 1))
		if err != nil && err.Matches(types.KEY_EXISTS_ERROR) {
			return nil // exists: same as add returning 0
		}
		return asErr(err)
	})
	run("update native", func(i, k int) error {
		p := as.NewWritePolicy(uint32(expected(i)), 0)
		p.TotalTimeout = wp.TotalTimeout
		p.GenerationPolicy = as.EXPECT_GEN_EQUAL
		p.RecordExistsAction = as.UPDATE_ONLY
		rec, err := c.Operate(p, key("native", k),
			as.PutOp(as.NewBin("qty", i)), as.AddOp(as.NewBin("version", 1)), as.GetBinOp("version"))
		if err != nil {
			return err // GENERATION_ERROR on a version mismatch
		}
		if v := rec.Bins["version"]; v != expected(i)+1 {
			return fmt.Errorf("key %d: version %v, want %d", k, v, expected(i)+1)
		}
		return nil
	})
	run("delete native", func(i, k int) error {
		_, err := c.Delete(wp, key("native", k))
		return asErr(err)
	})
}
