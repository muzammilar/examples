package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
	"sync"
	"time"
)

type subscriberPhase struct {
	Operations int       `json:"operations"`
	Elapsed    float64   `json:"elapsed_seconds"`
	Rate       float64   `json:"operations_per_second"`
	P50        float64   `json:"p50_ms"`
	P99        float64   `json:"p99_ms"`
	Max        float64   `json:"max_ms"`
	Samples    []float64 `json:"latency_samples_ms"`
}

func subscriberStats(samples []float64, elapsed time.Duration) subscriberPhase {
	sort.Float64s(samples)
	n := len(samples)
	return subscriberPhase{n, elapsed.Seconds(), float64(n) / elapsed.Seconds(), samples[max(0, int(math.Ceil(float64(n)*.5))-1)], samples[max(0, int(math.Ceil(float64(n)*.99))-1)], samples[n-1], samples}
}
func subscribers(db *sql.DB, f fixture) error {
	count, workers := f.Subscribers, number("WORKERS", 8)
	var config profileConfig
	if err := readJSON("/results/run.json", &config); err != nil {
		return err
	}
	operations := config.Operations
	if config.Version < 1 || operations < 1 || operations > count {
		return fmt.Errorf("invalid profile allocation")
	}
	ledger, err := profileHistory("/witness", count)
	if err != nil {
		return err
	}
	ctx, done := context.WithTimeout(context.Background(), 10*time.Minute)
	defer done()
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	report := checkReport{}
	err = verifySubscribers(ctx, conn, f, &report)
	conn.Close()
	if err != nil {
		return err
	}
	if report.Wrong+report.Missing+report.Duplicates+report.ProfileUnacked > 0 {
		return fmt.Errorf("resolve prior profile history before a new benchmark: %+v", report)
	}
	lookups := min(number("SUBSCRIBER_LOOKUPS", 10000), count)
	scans := min(number("SUBSCRIBER_SCANS", 20), count)
	queryBy := map[string]string{"primary_key": "SELECT quota,version FROM subscribers WHERE subscriber_id=?", "unique_phone": "SELECT quota,version FROM subscribers WHERE phone=?", "unindexed_reference": "SELECT quota,version FROM subscribers WHERE lookup_ref=?"}
	plans := map[string]map[string]string{}
	phases := map[string]subscriberPhase{}
	for _, mode := range []string{"primary_key", "unique_phone", "unindexed_reference"} {
		value := int64(42 % count)
		if mode != "primary_key" {
			value += 15550000000
		}
		rows, err := db.QueryContext(ctx, "EXPLAIN "+queryBy[mode], value)
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
			customerID := (i * 97) % count
			id := int64(customerID)
			if mode != "primary_key" {
				id += 15550000000
			}
			started := time.Now()
			ctx, cancel := context.WithTimeout(ctx, 15*time.Second)
			var quota, version int
			err = db.QueryRowContext(ctx, queryBy[mode], id).Scan(&quota, &version)
			cancel()
			if err != nil {
				return err
			}
			expectedVersion, expectedQuota := ledger.latestACK(customerID)
			if int64(quota) != expectedQuota || int64(version) != expectedVersion {
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
	journal, err := openJournal("profiles.jsonl")
	if err != nil {
		return err
	}
	defer journal.file.Close()
	jobs := make(chan int)
	outcomes := make(chan outcome, workers)
	ctx, cancel := context.WithCancel(ctx)
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
					previousVersion, previousQuota := ledger.latestACK(id)
					if previousQuota <= 0 || config.Version <= previousVersion {
						outcomes <- outcome{id, write, 0, fmt.Errorf("quota/version exhausted")}
						qcancel()
						continue
					}
					operation := event{Stage: "attempt", First: config.Version, Count: 1, Customer: id, Quota: previousQuota - 1, PreviousQuota: previousQuota, PreviousVersion: previousVersion, Started: time.Now().UTC()}
					err = journal.append(operation)
					if err == nil {
						var result sql.Result
						result, err = db.ExecContext(qctx, "UPDATE subscribers SET quota=?,version=? WHERE subscriber_id=? AND version=? AND quota=?", operation.Quota, config.Version, id, previousVersion, previousQuota)
						uncertain := err != nil
						if err == nil {
							var changed int64
							changed, err = result.RowsAffected()
							if err == nil && changed != 1 {
								err = fmt.Errorf("quota CAS affected %d rows", changed)
							}
						}
						operation.At = time.Now().UTC()
						operation.Latency = float64(time.Since(started)) / float64(time.Millisecond)
						if err == nil {
							operation.Stage = "ack"
						} else {
							operation.Stage = "error"
							operation.Ambiguous = uncertain
							operation.Error = err.Error()
						}
						if journalErr := journal.append(operation); journalErr != nil {
							err = journalErr
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
	var firstError error
	for item := range outcomes {
		if item.err != nil {
			if firstError == nil {
				firstError = item.err
				cancel()
			}
			continue
		}
		samples = append(samples, item.latency)
		if item.write {
			updates++
		}
	}
	if firstError != nil {
		return firstError
	}
	if len(samples) != operations || updates != (operations+4)/5 {
		return fmt.Errorf("incomplete subscriber operations")
	}
	phases["mixed_80_percent_reads_20_percent_updates"] = subscriberStats(samples, time.Since(start))
	check := checkReport{}
	verifyContext, verifyCancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer verifyCancel()
	conn, err = db.Conn(verifyContext)
	if err != nil {
		return err
	}
	err = verifySubscribers(verifyContext, conn, f, &check)
	conn.Close()
	if err != nil {
		return err
	}
	if check.Wrong+check.Missing+check.Duplicates+check.ProfileUnacked > 0 {
		return fmt.Errorf("profile full-body reconciliation failed: %+v", check)
	}
	data, err := json.MarshalIndent(map[string]any{"subscribers": count, "acknowledged_quota_updates": updates, "target_version": config.Version, "incorrect_rows": check.Wrong + check.Missing + check.Duplicates, "phases": phases, "plans": plans}, "", "  ")
	if err != nil {
		return err
	}
	fmt.Println(string(data))
	return os.WriteFile("/results/subscriber-profiles.json", append(data, '\n'), 0644)
}
