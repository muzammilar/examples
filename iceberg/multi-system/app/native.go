package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
)

type generation struct {
	Name      string `json:"name"`
	Rows      int64  `json:"rows"`
	Customers int64  `json:"customers"`
}

func nodeName(s, r int) string { return fmt.Sprintf("ch%d%d", s, r) }
func generationDDL(g generation) string {
	return fmt.Sprintf("CREATE TABLE IF NOT EXISTS default.%s (event_id Int64,customer_id Int32,amount_cents Int64) ENGINE=ReplicatedMergeTree('/iceberg-lab/{shard}/%s','{replica}') ORDER BY (customer_id,event_id)", g.Name, g.Name)
}
func eachNode(ctx context.Context, skip string, fn func(context.Context, clickhouse.Conn, int) error) error {
	for s := 1; s <= 3; s++ {
		for r := 1; r <= 3; r++ {
			name := nodeName(s, r)
			if name == skip {
				continue
			}
			c, e := chAt(name + ":9000")
			if e != nil {
				return e
			}
			e = fn(ctx, c, s)
			_ = c.Close()
			if e != nil {
				return fmt.Errorf("%s: %w", name, e)
			}
		}
	}
	return nil
}

// CREATE TABLE completes before a replicated table necessarily has a live Keeper session.
// Poll state only: never retry a possibly committed INSERT.
func waitWritable(ctx context.Context, c clickhouse.Conn, table string) error {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		var readonly, expired uint8
		if err := c.QueryRow(ctx, "SELECT is_readonly,is_session_expired FROM system.replicas WHERE database='default' AND table=? LIMIT 1", table).Scan(&readonly, &expired); err != nil {
			return err
		}
		if readonly == 0 && expired == 0 {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("replica %s readiness: %w", table, ctx.Err())
		case <-ticker.C:
		}
	}
}

func nativeVerify(ctx context.Context, g generation, skip string) error {
	coordinator, e := ch()
	if e != nil {
		return e
	}
	defer coordinator.Close()
	// Compute the exact deterministic distribution, not an assumed one-third split.
	var counts [3]uint64
	var sums [3]int64
	for shard := 0; shard < 3; shard++ {
		q := fmt.Sprintf("SELECT count(),sum(toInt64(number%%10000+1)) FROM numbers(%d) WHERE cityHash64(toInt32(number%%%d))%%3=%d LIMIT 1", g.Rows, g.Customers, shard)
		if e = coordinator.QueryRow(ctx, q).Scan(&counts[shard], &sums[shard]); e != nil {
			return e
		}
	}
	reports := []map[string]any{}
	e = eachNode(ctx, skip, func(ctx context.Context, c clickhouse.Conn, shard int) error {
		if e := waitWritable(ctx, c, g.Name); e != nil {
			return e
		}
		if e := c.Exec(ctx, "SYSTEM SYNC REPLICA default."+g.Name); e != nil {
			return e
		}
		var count, unique, bad uint64
		var sum int64
		q := fmt.Sprintf("SELECT count(),uniqExact(event_id),sum(amount_cents),countIf(event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR event_id<0 OR event_id>=%d OR customer_id!=event_id%%%d OR amount_cents!=event_id%%10000+1 OR cityHash64(customer_id)%%3!=%d) FROM default.%s LIMIT 1", g.Rows, g.Customers, shard-1, g.Name)
		if e := c.QueryRow(ctx, q).Scan(&count, &unique, &sum, &bad); e != nil {
			return e
		}
		if count != counts[shard-1] || unique != count || sum != sums[shard-1] || bad != 0 {
			return fmt.Errorf("replica oracle count/unique/sum/bad=%d/%d/%d/%d", count, unique, sum, bad)
		}
		var host string
		if err := c.QueryRow(ctx, "SELECT hostName()").Scan(&host); err != nil {
			return err
		}
		reports = append(reports, map[string]any{"host": host, "shard": shard, "rows": count, "expected_rows": counts[shard-1], "unique_ids": unique, "amount_cents": sum, "expected_amount_cents": sums[shard-1], "invalid_or_wrong_shard_rows": bad})
		return nil
	})
	if e != nil {
		return e
	}
	return save("ownership-"+g.Name, map[string]any{"generation": g, "skipped_node": skip, "replicas": reports})
}
func native(stage bool) error {
	n, e := positive("ROWS", 2000000)
	if e != nil {
		return e
	}
	customers, e := positive("CUSTOMERS", 10000)
	if e != nil {
		return e
	}
	g := generation{fmt.Sprintf("serving_%d", time.Now().UnixNano()), n, customers}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	start := time.Now()
	skip := env("SKIP_NODE", "")
	if stage != (skip != "") {
		return fmt.Errorf("staged refresh requires one skipped node; normal publication requires all nine")
	}
	if skip != "" && !regexp.MustCompile(`^ch[1-3][1-3]$`).MatchString(skip) {
		return fmt.Errorf("invalid SKIP_NODE")
	}
	if e = eachNode(ctx, skip, func(ctx context.Context, c clickhouse.Conn, _ int) error {
		if e := c.Exec(ctx, generationDDL(g)); e != nil {
			return e
		}
		return waitWritable(ctx, c, g.Name)
	}); e != nil {
		return e
	}
	c, e := ch()
	if e != nil {
		return e
	}
	defer c.Close()
	q := fmt.Sprintf("CREATE TABLE default.%s_dist (event_id Int64,customer_id Int32,amount_cents Int64) ENGINE=Distributed(lake_serving,default,%s,cityHash64(customer_id))", g.Name, g.Name)
	if e = c.Exec(ctx, q); e != nil {
		return e
	}
	// Source writers are fenced during a refresh; all rows come from one committed
	// table version. A new unpublished generation isolates partial shard refreshes.
	q = fmt.Sprintf("INSERT INTO default.%s_dist SELECT event_id,customer_id,amount_cents FROM lake.`lab.events` SETTINGS insert_distributed_sync=1,insert_quorum=2,insert_quorum_timeout=120000", g.Name)
	if e = c.Exec(ctx, q); e != nil {
		return e
	}
	if e = nativeVerify(ctx, g, skip); e != nil {
		return e
	}
	if stage {
		if e = save("staged-generation", g); e != nil {
			return e
		}
		// The prior published generation must stay usable during the replica outage.
		var count uint64
		if e = c.QueryRow(ctx, "SELECT count() FROM default.serving_current LIMIT 1").Scan(&count); e != nil {
			return e
		}
		batch, e := positive("BATCH_ROWS", 100000)
		if e != nil {
			return e
		}
		if count != uint64(n-batch) {
			return fmt.Errorf("old serving generation changed: %d", count)
		}
		return save("replica-outage", map[string]any{"source_rows": n, "published_rows": count, "verified_replicas": 8, "published_new_generation": false})
	}
	if e = publish(ctx, c, g); e != nil {
		return e
	}
	return save("native", map[string]any{"rows": n, "shards": 3, "replicas_per_shard": 3, "verified_replicas": 9, "seconds": time.Since(start).Seconds(), "generation": g.Name, "missing_ids": 0, "duplicate_ids": 0})
}
func publish(ctx context.Context, c clickhouse.Conn, g generation) error {
	if e := c.Exec(ctx, fmt.Sprintf("CREATE OR REPLACE VIEW default.serving_current AS SELECT event_id,customer_id,amount_cents FROM default.%s_dist", g.Name)); e != nil {
		return e
	}
	var count, unique uint64
	var sum int64
	if e := c.QueryRow(ctx, "SELECT count(),uniqExact(event_id),sum(amount_cents) FROM default.serving_current LIMIT 1").Scan(&count, &unique, &sum); e != nil {
		return e
	}
	expected := g.Rows/10000*50005000 + (g.Rows%10000)*(g.Rows%10000+1)/2
	if count != uint64(g.Rows) || unique != count || sum != expected {
		return fmt.Errorf("published generation oracle failed")
	}
	return nil
}
func recoverNative() error {
	b, e := os.ReadFile(filepath.Join(env("RESULTS_DIR", "/results"), "staged-generation.json"))
	if e != nil {
		return e
	}
	var g generation
	if e = json.Unmarshal(b, &g); e != nil {
		return e
	}
	if !regexp.MustCompile(`^serving_[0-9]+$`).MatchString(g.Name) {
		return fmt.Errorf("invalid generation name")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	start := time.Now()
	if e = eachNode(ctx, "", func(ctx context.Context, c clickhouse.Conn, _ int) error {
		if e := c.Exec(ctx, generationDDL(g)); e != nil {
			return e
		}
		return waitWritable(ctx, c, g.Name)
	}); e != nil {
		return e
	}
	if e = nativeVerify(ctx, g, ""); e != nil {
		return e
	}
	c, e := ch()
	if e != nil {
		return e
	}
	defer c.Close()
	if e = publish(ctx, c, g); e != nil {
		return e
	}
	return save("replica-recovery", map[string]any{"rows": g.Rows, "verified_replicas": 9, "seconds": time.Since(start).Seconds(), "missing_ids": 0, "duplicate_ids": 0})
}
func appendSource() error {
	n, e := positive("ROWS", 2000000)
	if e != nil {
		return e
	}
	customers, e := positive("CUSTOMERS", 10000)
	if e != nil {
		return e
	}
	batch, e := positive("BATCH_ROWS", 100000)
	if e != nil {
		return e
	}
	db, e := duck(true)
	if e != nil {
		return e
	}
	defer db.Close()
	_, e = db.Exec(fmt.Sprintf("INSERT INTO lake.lab.events SELECT i,CAST(i%%%d AS INTEGER),i%%10000+1 FROM range(%d,%d) t(i)", customers, n, n+batch))
	if e != nil {
		return fmt.Errorf("source append unresolved; no blind retry: %w", e)
	}
	return save("source-append", map[string]any{"rows_before": n, "acknowledged_added_rows": batch, "rows_after": n + batch})
}
