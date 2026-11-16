package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

func insert(ctx context.Context, tx *sql.Tx, first int64, n, customer int) error {
	var text strings.Builder
	text.WriteString("INSERT INTO orders(customer_id,op_id,amount) VALUES")
	args := make([]any, 0, n*3)
	for i := 0; i < n; i++ {
		if i > 0 {
			text.WriteByte(',')
		}
		fmt.Fprintf(&text, "($%d,$%d,$%d)", i*3+1, i*3+2, i*3+3)
		id := first + int64(i)
		args = append(args, customer, id, id%10000+1)
	}
	text.WriteString(" ON CONFLICT(customer_id,op_id) DO NOTHING")
	result, err := tx.ExecContext(ctx, text.String(), args...)
	if err != nil {
		return err
	}
	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected > int64(n) {
		return fmt.Errorf("bulk INSERT affected %d rows for %d intended keys; refusing broadcast commit", affected, n)
	}
	return nil
}
func load(db *sql.DB, c runConfig, route []int) error {
	if c.Base < 0 || c.Reserved < 1 || c.Minimum < 1 || c.Minimum > c.Reserved || c.Batch < 1 || c.Batch > 10000 || c.Customers != len(route) {
		return errors.New("invalid run allocation")
	}
	j, err := openJournal("writes.jsonl")
	if err != nil {
		return err
	}
	defer j.file.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	jobs := make(chan int)
	workers := number("WORKERS", 8)
	failures := make(chan error, workers)
	var wg sync.WaitGroup
	var acked, retries, ambiguous atomic.Int64
	started := time.Now()
	for w := 0; w < workers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for offset := range jobs {
				first := c.Base + int64(offset)
				n := min(c.Batch, c.Reserved-offset)
				customer := (offset / c.Batch) % c.Customers
				batchStart := time.Now()
				for attempt := 1; ; attempt++ {
					if err := ctx.Err(); err != nil {
						failures <- err
						return
					}
					operation := event{Stage: "attempt", First: first, Count: n, Customer: customer, Shard: route[customer], Attempt: attempt, Started: time.Now().UTC()}
					if err := j.append(operation); err != nil {
						cancel()
						failures <- err
						return
					}
					requestStart := time.Now()
					operation.Started = requestStart.UTC()
					qctx, qcancel := context.WithTimeout(ctx, 15*time.Second)
					tx, e := db.BeginTx(qctx, nil)
					uncertain := false
					if e == nil {
						e = insert(qctx, tx, first, n, customer)
						if e == nil {
							e = tx.Commit()
							uncertain = e != nil
						} else {
							_ = tx.Rollback()
						}
					}
					qcancel()
					operation.At = time.Now().UTC()
					operation.Latency = float64(time.Since(requestStart)) / float64(time.Millisecond)
					if e == nil {
						operation.Stage = "ack"
						operation.BatchLatency = float64(time.Since(batchStart)) / float64(time.Millisecond)
					} else {
						operation.Stage = "error"
						operation.Ambiguous = uncertain
						operation.Error = e.Error()
						if len(operation.Error) > 1000 {
							operation.Error = operation.Error[:1000]
						}
					}
					if err := j.append(operation); err != nil {
						cancel()
						failures <- err
						return
					}
					if e == nil {
						acked.Add(int64(n))
						break
					}
					retries.Add(1)
					if uncertain {
						ambiguous.Add(1)
					}
					if time.Since(batchStart) > 3*time.Minute {
						cancel()
						failures <- fmt.Errorf("batch %d exceeded retry deadline: %w", first, e)
						return
					}
					select {
					case <-time.After(100 * time.Millisecond):
					case <-ctx.Done():
						failures <- ctx.Err()
						return
					}
				}
			}
		}()
	}
	sent := 0
	producerDone := false
	var ticker *time.Ticker
	var ticks <-chan time.Time
	if os.Getenv("RATE") != "" && os.Getenv("RATE") != "0" {
		ticker = time.NewTicker(max(time.Microsecond, time.Duration(float64(time.Second)*float64(c.Batch)/float64(number("RATE", 25000)))))
		ticks = ticker.C
		defer ticker.Stop()
	}
produce:
	for offset := 0; offset < c.Reserved; offset += c.Batch {
		if stopped("abort-writer") {
			cancel()
			break
		}
		if c.Live && offset >= c.Minimum && stopped("stop-writer") {
			producerDone = true
			break
		}
		if ticks != nil {
			select {
			case <-ticks:
			case <-ctx.Done():
				break produce
			}
		}
		select {
		case jobs <- offset:
			sent += min(c.Batch, c.Reserved-offset)
		case <-ctx.Done():
			break produce
		}
	}
	close(jobs)
	wg.Wait()
	close(failures)
	for err := range failures {
		if err != nil {
			return err
		}
	}
	if c.Live && !producerDone {
		return errors.New("live writer exhausted reserved IDs before controller stopped it; increase MAX_ROWS")
	}
	if int(acked.Load()) != sent || sent < c.Minimum {
		return fmt.Errorf("incomplete load %d/%d rows, minimum %d", acked.Load(), sent, c.Minimum)
	}
	return save("/results/load.json", map[string]any{"acknowledged_rows": acked.Load(), "seconds": time.Since(started).Seconds(), "rows_per_second": float64(acked.Load()) / time.Since(started).Seconds(), "retry_attempts": retries.Load(), "ambiguous_commit_responses": ambiguous.Load(), "minimum_rows": c.Minimum, "reserved_rows": c.Reserved})
}
