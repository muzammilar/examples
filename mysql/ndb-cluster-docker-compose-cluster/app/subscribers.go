package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

type subscriberPhase struct {
	Operations int     `json:"operations"`
	Elapsed    float64 `json:"elapsed_seconds"`
	Rate       float64 `json:"operations_per_second"`
	P50        float64 `json:"p50_ms"`
	P99        float64 `json:"p99_ms"`
	Max        float64 `json:"max_ms"`
}

func subscriberStats(samples []float64, elapsed time.Duration) subscriberPhase {
	sort.Float64s(samples)
	n := len(samples)
	return subscriberPhase{n, elapsed.Seconds(), float64(n) / elapsed.Seconds(), samples[(n-1)/2], samples[(n-1)*99/100], samples[n-1]}
}
func subscribers(db *sql.DB, count, workers int) error {
	lookups := min(number("SUBSCRIBER_LOOKUPS", 10000), count)
	scans := min(number("SUBSCRIBER_SCANS", 20), count)
	operations := min(number("SUBSCRIBER_OPERATIONS", 10000), count)
	if _, err := db.Exec("TRUNCATE TABLE subscribers"); err != nil {
		return err
	}
	seedStart := time.Now()
	for first := 0; first < count; first += 500 {
		n := min(500, count-first)
		var b strings.Builder
		b.WriteString("INSERT INTO subscribers(subscriber_id,phone,lookup_ref,quota,version) VALUES")
		args := make([]any, 0, n*3)
		for i := 0; i < n; i++ {
			if i > 0 {
				b.WriteByte(',')
			}
			b.WriteString("(?,?,?,1000,0)")
			id := first + i
			args = append(args, id, 15550000000+int64(id), 15550000000+int64(id))
		}
		ctx, cancel := queryContext()
		_, err := db.ExecContext(ctx, b.String(), args...)
		cancel()
		if err != nil {
			return err
		}
	}
	seedSeconds := time.Since(seedStart).Seconds()
	queryBy := map[string]string{"primary_key": "SELECT quota,version FROM subscribers WHERE subscriber_id=?", "unique_phone": "SELECT quota,version FROM subscribers WHERE phone=?", "unindexed_reference": "SELECT quota,version FROM subscribers WHERE lookup_ref=?"}
	plans := map[string]map[string]string{}
	phases := map[string]subscriberPhase{}
	for _, mode := range []string{"primary_key", "unique_phone", "unindexed_reference"} {
		value := int64(42 % count)
		if mode != "primary_key" {
			value += 15550000000
		}
		rows, err := db.Query("EXPLAIN "+queryBy[mode], value)
		if err != nil {
			return err
		}
		columns, err := rows.Columns()
		if err != nil {
			rows.Close()
			return err
		}
		plan := map[string]string{}
		if rows.Next() {
			values := make([]sql.NullString, len(columns))
			dest := make([]any, len(columns))
			for i := range values {
				dest[i] = &values[i]
			}
			err = rows.Scan(dest...)
			for i, name := range columns {
				if values[i].Valid {
					plan[name] = values[i].String
				}
			}
		}
		if err == nil {
			err = rows.Err()
		}
		rows.Close()
		if err != nil {
			return err
		}
		if mode == "primary_key" && plan["key"] != "PRIMARY" {
			return fmt.Errorf("primary lookup plan: %v", plan)
		}
		if mode == "unique_phone" && plan["key"] != "phone" {
			return fmt.Errorf("phone lookup plan: %v", plan)
		}
		if mode == "unindexed_reference" && plan["key"] != "" {
			return fmt.Errorf("expected a scan: %v", plan)
		}
		plans[mode] = plan
		repetitions := lookups
		if mode == "unindexed_reference" {
			repetitions = scans
		}
		samples := make([]float64, 0, repetitions)
		start := time.Now()
		for i := 0; i < repetitions; i++ {
			id := int64((i * 97) % count)
			if mode != "primary_key" {
				id += 15550000000
			}
			started := time.Now()
			ctx, cancel := queryContext()
			var quota, version int
			err = db.QueryRowContext(ctx, queryBy[mode], id).Scan(&quota, &version)
			cancel()
			if err != nil {
				return err
			}
			if quota != 1000 || version != 0 {
				return fmt.Errorf("incorrect subscriber state")
			}
			samples = append(samples, float64(time.Since(started).Microseconds())/1000)
		}
		phases[mode] = subscriberStats(samples, time.Since(start))
	}
	type outcome struct {
		id      int
		write   bool
		latency float64
		err     error
	}
	file, err := os.OpenFile("/results/subscriber-acknowledged.jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer file.Close()
	encoder := json.NewEncoder(file)
	jobs := make(chan int)
	outcomes := make(chan outcome, workers)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	var wg sync.WaitGroup
	start := time.Now()
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for id := range jobs {
				started := time.Now()
				qctx, qcancel := context.WithTimeout(ctx, 15*time.Second)
				write := id%5 == 0
				var err error
				if write {
					var result sql.Result
					result, err = db.ExecContext(qctx, "UPDATE subscribers SET quota=quota-1,version=version+1 WHERE subscriber_id=? AND version=0 AND quota>0", id)
					if err == nil {
						var changed int64
						changed, err = result.RowsAffected()
						if err == nil && changed != 1 {
							err = fmt.Errorf("quota update affected %d rows", changed)
						}
					}
				} else {
					var quota, version int
					err = db.QueryRowContext(qctx, queryBy["primary_key"], id).Scan(&quota, &version)
					if err == nil && (quota != 1000 || version != 0) {
						err = fmt.Errorf("mixed read mismatch")
					}
				}
				qcancel()
				outcomes <- outcome{id, write, float64(time.Since(started).Microseconds()) / 1000, err}
			}
		}()
	}
	go func() {
		defer close(jobs)
		for id := 0; id < operations; id++ {
			select {
			case jobs <- id:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() { wg.Wait(); close(outcomes) }()
	samples := []float64{}
	updates := 0
	for item := range outcomes {
		if item.err != nil {
			return item.err
		}
		samples = append(samples, item.latency)
		if item.write {
			if err = encoder.Encode(map[string]int{"subscriber_id": item.id, "quota": 999, "version": 1}); err != nil {
				return err
			}
			if err = file.Sync(); err != nil {
				return err
			}
			updates++
		}
	}
	if len(samples) != operations || updates != (operations+4)/5 {
		return fmt.Errorf("incomplete subscriber operations")
	}
	phases["mixed_80_percent_reads_20_percent_updates"] = subscriberStats(samples, time.Since(start))
	var rows, versionSum, bad int
	var quotaSum int64
	err = db.QueryRow("SELECT count(*),sum(quota),sum(version),sum(quota<>1000-IF(subscriber_id<? AND MOD(subscriber_id,5)=0,1,0) OR version<>IF(subscriber_id<? AND MOD(subscriber_id,5)=0,1,0)) FROM subscribers", operations, operations).Scan(&rows, &quotaSum, &versionSum, &bad)
	if err != nil {
		return err
	}
	if rows != count || versionSum != updates || quotaSum != int64(count)*1000-int64(updates) || bad != 0 {
		return fmt.Errorf("subscriber reconciliation failed: rows=%d quota=%d versions=%d bad=%d", rows, quotaSum, versionSum, bad)
	}
	data, err := json.MarshalIndent(map[string]any{"subscribers": count, "seed_seconds": seedSeconds, "acknowledged_quota_updates": updates, "observed_versions": versionSum, "quota_sum": quotaSum, "incorrect_rows": bad, "phases": phases, "plans": plans}, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return os.WriteFile("/results/subscriber-profiles.json", append(data, '\n'), 0644)
}
