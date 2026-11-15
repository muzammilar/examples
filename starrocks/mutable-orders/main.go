package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

var results = env("RESULTS", "/results")

type order struct {
	Customer    uint32 `json:"customer_id"`
	ID          uint64 `json:"order_id"`
	Amount      uint32 `json:"amount"`
	Status      string `json:"status"`
	Version     uint64 `json:"version"`
	Fingerprint string `json:"fingerprint"`
}

type customer struct {
	ID     uint32 `json:"customer_id"`
	Region string `json:"region"`
}

func check(err error) {
	if err != nil {
		panic(err)
	}
}
func save(name string, value any) {
	text, err := json.MarshalIndent(value, "", "  ")
	check(err)
	path := filepath.Join(results, name+".json")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	check(err)
	_, err = file.Write(append(text, '\n'))
	check(err)
	check(file.Sync())
	check(file.Close())
	fmt.Println("saved", filepath.Join(results, name+".json"))
}
func rows(system, text string) [][]string { data, err := query(system, text); check(err); return data }
func uintValue(text string) uint64        { n, err := strconv.ParseUint(text, 10, 64); check(err); return n }
func count() int {
	n, err := strconv.Atoi(env("COUNT", "1000000"))
	check(err)
	if n < 10000 || n > 10000000 {
		panic("COUNT must be 10,000..10,000,000")
	}
	return n
}
func systemNames() []string {
	names := strings.Split(env("SYSTEMS", "starrocks,clickhouse"), ",")
	seen := map[string]bool{}
	for _, name := range names {
		if (name != "starrocks" && name != "clickhouse") || seen[name] {
			panic("invalid SYSTEMS")
		}
		seen[name] = true
	}
	return names
}
func enabled(name string) bool {
	for _, system := range systemNames() {
		if name == system {
			return true
		}
	}
	return false
}

func topologyNumber(key string, fallback int) int {
	n, err := strconv.Atoi(env(key, strconv.Itoa(fallback)))
	check(err)
	if n < 1 || n > 7 {
		panic("invalid topology count: " + key)
	}
	return n
}
func srCopies() int { return topologyNumber("SR_REPLICAS", 3) }
func chShards() int { return topologyNumber("CH_SHARDS", 3) }
func chCopies() int { return topologyNumber("CH_REPLICAS", 3) }

func fingerprint(id uint64, prefix string) string {
	h := sha256.Sum256([]byte(fmt.Sprintf("%s:%d", prefix, id)))
	return hex.EncodeToString(h[:])
}
func expected(id uint64, updated bool) order {
	row := order{uint32(id % 10000), id, uint32((id * 17) % 100000), "created", 1, fingerprint(id, "order")}
	if updated && id%10 == 0 {
		row.Amount += 42
		row.Status = "shipped"
		row.Version = 2
	}
	return row
}
func journal(file *os.File, value any) {
	check(json.NewEncoder(file).Encode(value))
	check(file.Sync())
}

func setup() {
	if _, err := os.Stat(filepath.Join(results, "seed.json")); err == nil {
		return
	} else if !os.IsNotExist(err) {
		check(err)
	}
	if enabled("starrocks") {
		check(execSQL("starrocks", "CREATE DATABASE IF NOT EXISTS orders_lab"))
		for _, table := range []string{"orders", "recovery"} {
			check(execSQL("starrocks", fmt.Sprintf(`CREATE TABLE IF NOT EXISTS orders_lab.%s (customer_id INT NOT NULL,order_id BIGINT NOT NULL,amount INT NOT NULL,status VARCHAR(16) NOT NULL,version BIGINT NOT NULL,fingerprint VARCHAR(64) NOT NULL) PRIMARY KEY(customer_id,order_id) DISTRIBUTED BY HASH(customer_id) BUCKETS 3 PROPERTIES("replication_num"="%d","enable_persistent_index"="true","write_quorum"="MAJORITY")`, table, srCopies())))
		}
		check(execSQL("starrocks", fmt.Sprintf(`CREATE TABLE IF NOT EXISTS orders_lab.customers (customer_id INT NOT NULL,region VARCHAR(16) NOT NULL) PRIMARY KEY(customer_id) DISTRIBUTED BY HASH(customer_id) BUCKETS 3 PROPERTIES("replication_num"="%d","enable_persistent_index"="true","write_quorum"="MAJORITY")`, srCopies())))
	}
	if enabled("clickhouse") {
		check(execSQL("clickhouse", "CREATE DATABASE IF NOT EXISTS orders_lab ON CLUSTER orders_cluster"))
		for _, table := range []string{"orders", "recovery"} {
			check(execSQL("clickhouse", fmt.Sprintf(`CREATE TABLE IF NOT EXISTS orders_lab.%s_local ON CLUSTER orders_cluster (customer_id UInt32,order_id UInt64,amount UInt32,status LowCardinality(String),version UInt64,fingerprint FixedString(64)) ENGINE=ReplicatedReplacingMergeTree('/orders_lab/{shard}/%s','{replica}',version) ORDER BY(customer_id,order_id) SETTINGS fsync_after_insert=1,fsync_part_directory=1`, table, table)))
			check(execSQL("clickhouse", fmt.Sprintf(`CREATE TABLE IF NOT EXISTS orders_lab.%s ON CLUSTER orders_cluster AS orders_lab.%s_local ENGINE=Distributed(orders_cluster,orders_lab,%s_local,customer_id)`, table, table, table)))
		}
		check(execSQL("clickhouse", `CREATE TABLE IF NOT EXISTS orders_lab.customers_local ON CLUSTER orders_cluster (customer_id UInt32,region LowCardinality(String)) ENGINE=ReplicatedMergeTree('/orders_lab/{shard}/customers','{replica}') ORDER BY customer_id SETTINGS fsync_after_insert=1,fsync_part_directory=1`))
	}
	var customers []customer
	for i := uint32(0); i < 10000; i++ {
		customers = append(customers, customer{i, fmt.Sprintf("region-%d", i%10)})
	}
	if enabled("starrocks") {
		check(streamLoad("customers", "customers-"+strconv.FormatInt(time.Now().UnixNano(), 10), customers))
	}
	if enabled("clickhouse") {

		for shard := 0; shard < chShards(); shard++ {
			response, err := chRequest(fmt.Sprintf("ch%d0", shard), `INSERT INTO orders_lab.customers_local SELECT toUInt32(number),concat('region-',toString(number%10)) FROM numbers(10000)`, nil)
			check(err)
			check(response.Body.Close())
		}
	}

	save("setup", map[string]any{"customers": 10000, "starrocks_buckets": 3, "starrocks_copies": srCopies(), "clickhouse_shards": chShards(), "clickhouse_copies": chCopies()})
}

func seed() {
	n := count()
	if _, err := os.Stat(filepath.Join(results, "seed.json")); !os.IsNotExist(err) {
		panic("existing seed; archive results before a fresh lifecycle")
	}
	file, err := os.OpenFile(filepath.Join(results, "seed-journal.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	out := map[string]any{}
	for _, system := range systemNames() {
		phases := map[string]any{}
		for _, phase := range []string{"initial", "updates", "stale-replay"} {
			start := time.Now()
			var lat []float64
			loaded := 0
			batch := make([]order, 0, 10000)
			flush := func() {
				if len(batch) == 0 {
					return
				}
				label := fmt.Sprintf("%s-%s-%d-%d", system, phase, start.UnixNano(), loaded)
				journal(file, map[string]any{"system": system, "phase": phase, "stage": "attempt", "label": label, "rows": len(batch), "at": time.Now().UTC()})
				begin := time.Now()
				check(insertRows(system, "orders", label, batch))
				lat = append(lat, float64(time.Since(begin))/float64(time.Millisecond))
				journal(file, map[string]any{"system": system, "phase": phase, "stage": "ack", "label": label, "rows": len(batch), "at": time.Now().UTC()})
				loaded += len(batch)
				batch = batch[:0]
			}
			for id := uint64(0); id < uint64(n); id++ {
				if phase != "initial" && id%10 != 0 {
					continue
				}
				batch = append(batch, expected(id, phase == "updates"))
				if len(batch) == cap(batch) {
					flush()
				}
			}
			flush()
			sort.Float64s(lat)
			elapsed := time.Since(start).Seconds()
			phases[phase] = map[string]any{"accepted_input_rows": loaded, "seconds": elapsed, "rows_per_second": float64(loaded) / elapsed, "batch_p50_ms": lat[len(lat)/2], "batch_p99_ms": lat[min(len(lat)-1, int(float64(len(lat))*.99))], "errors": 0}
		}
		out[system] = phases
	}
	save("seed", out)
}

func parseOrder(values []string) order {
	if len(values) != 6 {
		panic("unexpected order schema")
	}
	return order{uint32(uintValue(values[0])), uintValue(values[1]), uint32(uintValue(values[2])), values[3], uintValue(values[4]), values[5]}
}

func verifyOrders(system, from string, n int) int {
	seen := make([]bool, n)
	total := 0
	check(readOrders(system, from, n+1, func(values []string) error {
		row := parseOrder(values)
		if row.ID >= uint64(n) || seen[row.ID] || row != expected(row.ID, true) {
			return fmt.Errorf("wrong, unexpected or duplicate order: %s %+v", system, row)
		}
		seen[row.ID] = true
		total++
		return nil
	}))
	if total != n {
		panic(fmt.Sprintf("%s missing orders: %d/%d", system, total, n))
	}
	return total
}

func verify() {
	n := count()
	out := map[string]any{}
	out["durability"] = verifyDurability()
	verifyDimensions()
	if enabled("starrocks") {
		out["starrocks_rows"] = verifyOrders("starrocks", "orders_lab.orders", count())
		verifyStarRocksPlacement()
	}
	if enabled("clickhouse") {
		out["clickhouse_rows"] = verifyOrders("clickhouse", "orders_lab.orders FINAL", count())
	}
	if enabled("clickhouse") {
		for shard := 0; shard < chShards(); shard++ {
			for replica := 0; replica < chCopies(); replica++ {
				host := fmt.Sprintf("ch%d%d", shard, replica)
				old := os.Getenv("CH_HOST")
				check(os.Setenv("CH_HOST", host))
				expectedN := 0
				seen := map[uint64]bool{}
				check(readOrders("clickhouse", "orders_lab.orders_local FINAL", n+1, func(values []string) error {
					row := parseOrder(values)
					if int(row.Customer)%chShards() != shard || row.ID >= uint64(n) || seen[row.ID] || row != expected(row.ID, true) {
						return fmt.Errorf("wrong replica placement/body on %s: %+v", host, row)
					}
					seen[row.ID] = true
					return nil
				}))
				for id := 0; id < n; id++ {
					if (id%10000)%chShards() == shard {
						expectedN++
					}
				}
				if len(seen) != expectedN {
					panic(fmt.Sprintf("missing rows on %s: %d/%d", host, len(seen), expectedN))
				}
				out[host] = len(seen)
				check(os.Setenv("CH_HOST", old))
			}
		}
	}
	out["missing"], out["wrong_values"], out["duplicate_logical_rows"] = 0, 0, 0
	save("correctness", out)
	verifyRecovery()
}

func benchmark() {
	n := count()
	var customerSum, total uint64
	for id := uint64(0); id < uint64(n); id++ {
		row := expected(id, true)
		total += uint64(row.Amount)
		if row.Customer == 40 {
			customerSum += uint64(row.Amount)
		}
	}
	out := map[string]any{}
	for _, system := range systemNames() {
		from := "orders_lab.orders"
		if system == "clickhouse" {
			from += " FINAL"
		}
		queries := []struct {
			name, text string
			sum        uint64
		}{{"customer-current-state", "SELECT COALESCE(sum(amount),0) FROM " + from + " WHERE customer_id=40", customerSum}, {"all-current-state", "SELECT COALESCE(sum(amount),0) FROM " + from + " WHERE customer_id>=0 AND customer_id<10000", total}}
		for _, q := range queries {
			var lat []float64
			for run := 0; run < 21; run++ {
				start := time.Now()
				values := rows(system, q.text)
				elapsed := float64(time.Since(start)) / float64(time.Millisecond)
				if len(values) != 1 || uintValue(values[0][0]) != q.sum {
					panic("wrong benchmark result")
				}
				if run > 0 {
					lat = append(lat, elapsed)
				}
			}
			sort.Float64s(lat)
			out[system+"/"+q.name] = map[string]any{"samples": 20, "warmup": 1, "p50_ms": (lat[9] + lat[10]) / 2, "p99_ms": lat[19], "errors": 0}
		}
		var sums [10]uint64
		for id := uint64(0); id < uint64(n); id++ {
			row := expected(id, true)
			sums[row.Customer%10] += uint64(row.Amount)
		}
		dimension := "orders_lab.customers"
		join := "LEFT JOIN"
		if system == "clickhouse" {
			dimension = "orders_lab.customers_local"
			join = "LEFT ANY JOIN"
		}
		text := "SELECT region,sum(amount) FROM (SELECT customer_id,amount FROM " + from + " WHERE customer_id>=0 AND customer_id<10000) AS o " + join + " " + dimension + " USING(customer_id) GROUP BY region ORDER BY region LIMIT 10"
		var lat []float64
		for run := 0; run < 21; run++ {
			start := time.Now()
			values := rows(system, text)
			elapsed := float64(time.Since(start)) / float64(time.Millisecond)
			if len(values) != 10 {
				panic("missing joined regions")
			}
			for i, row := range values {
				if row[0] != fmt.Sprintf("region-%d", i) || uintValue(row[1]) != sums[i] {
					panic(fmt.Sprintf("incorrect %s joined aggregate: %v; expected region-%d sum %d", system, row, i, sums[i]))
				}
			}
			if run > 0 {
				lat = append(lat, elapsed)
			}
		}
		sort.Float64s(lat)
		out[system+"/region-join"] = map[string]any{"samples": 20, "warmup": 1, "p50_ms": (lat[9] + lat[10]) / 2, "p99_ms": lat[19], "errors": 0}
	}
	save("query-benchmark", out)
}

func drop() {
	if enabled("starrocks") {
		check(execSQL("starrocks", "DROP DATABASE IF EXISTS orders_lab FORCE"))
	}
	if enabled("clickhouse") {
		check(execSQL("clickhouse", "DROP DATABASE IF EXISTS orders_lab ON CLUSTER orders_cluster SYNC"))
	}
	save("cleanup", map[string]any{"workload_dropped": true})
}

func main() {
	check(os.MkdirAll(results, 0755))
	if len(os.Args) < 2 {
		panic("usage: client setup|seed|verify|benchmark|drop|sql")
	}
	switch os.Args[1] {
	case "setup":
		setup()
	case "seed":
		seed()
	case "verify":
		verify()
	case "benchmark":
		benchmark()
	case "membership":
		nativeStatus()
	case "replica-health":
		for _, table := range []string{"orders", "customers", "recovery"} {
			srReplicaChecksums(table)
		}
	case "churn":
		churn()
	case "watch":
		watch()
	case "verify-recovery":
		verifyRecovery()
	case "drop":
		drop()
	case "sql":
		if len(os.Args) != 4 {
			panic("sql SYSTEM QUERY")
		}
		value := rows(os.Args[2], os.Args[3])
		check(json.NewEncoder(os.Stdout).Encode(value))
	case "metadata":
		if len(os.Args) != 3 {
			panic("metadata QUERY")
		}
		value, err := srMetadata(os.Args[2])
		check(err)
		check(json.NewEncoder(os.Stdout).Encode(value))
	case "exec":
		if len(os.Args) != 4 {
			panic("exec SYSTEM STATEMENT")
		}
		check(execSQL(os.Args[2], os.Args[3]))
	default:
		panic("unknown command")
	}
}
