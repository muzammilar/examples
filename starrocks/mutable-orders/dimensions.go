package main

import (
	"fmt"
	"os"
)

// ANY joins require one correct dimension row per key; a matching aggregate is
// insufficient because duplicates could be silently hidden by ANY semantics.
func verifyDimensions() {
	verify := func(system, table string) {
		seen := make([]bool, 10000)
		check(readRows(system, "SELECT customer_id,region FROM "+table+" WHERE customer_id>=0 AND customer_id<10000 LIMIT 10001", func(values []string) error {
			if len(values) != 2 {
				return fmt.Errorf("invalid dimension schema")
			}
			id := uintValue(values[0])
			if id >= 10000 || seen[id] || values[1] != fmt.Sprintf("region-%d", id%10) {
				return fmt.Errorf("wrong/duplicate dimension: %v", values)
			}
			seen[id] = true
			return nil
		}))
		for id, found := range seen {
			if !found {
				panic(fmt.Sprintf("missing dimension %d", id))
			}
		}
	}
	if enabled("starrocks") {
		verify("starrocks", "orders_lab.customers")
	}
	if enabled("clickhouse") {
		old := os.Getenv("CH_HOST")
		defer func() { check(os.Setenv("CH_HOST", old)) }()
		for shard := 0; shard < chShards(); shard++ {
			for replica := 0; replica < chCopies(); replica++ {
				check(os.Setenv("CH_HOST", fmt.Sprintf("ch%d%d", shard, replica)))
				verify("clickhouse", "orders_lab.customers_local")
			}
		}
	}
}
