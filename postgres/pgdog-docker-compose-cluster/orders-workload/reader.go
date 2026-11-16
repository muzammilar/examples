package main

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"
)

type sample struct {
	id       int64
	customer int
	order    bool
}

func watch(db *sql.DB, f fixture, route []int) error {
	l, err := history("/witness", route)
	if err != nil {
		return err
	}
	samples := []sample{}
	customers := make(map[int]bool)
	// Sample different customers from immutable historical ACKs. The current
	// writer starts only after this independent reader has loaded its history.
collect:
	for _, run := range l.runs {
		for offset, flags := range run.flags {
			c := (offset / run.config.Batch) % f.Customers
			if flags&acknowledged != 0 && !customers[c] {
				samples = append(samples, sample{run.config.Base + int64(offset), c, true})
				customers[c] = true
				if len(samples) == 16 {
					break collect
				}
			}
		}
	}
	if len(samples) == 0 {
		samples = append(samples, sample{customer: 0})
	}
	j, err := openJournal("reads.jsonl")
	if err != nil {
		return err
	}
	defer j.file.Close()
	deadline := time.Now().Add(30 * time.Minute)
	for index := 0; !stopped("stop-reader"); index++ {
		if time.Now().After(deadline) {
			return errors.New("reader deadline exceeded")
		}
		key := samples[index%len(samples)]
		e := event{Stage: "read", Started: time.Now().UTC(), First: key.id, Customer: key.customer, Shard: 0, Outcome: "ok"}
		started := time.Now()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		if key.order {
			var customer int
			var amount int64
			err = db.QueryRowContext(ctx, "SELECT customer_id,amount FROM orders WHERE customer_id=$1 AND op_id=$2", key.customer, key.id).Scan(&customer, &amount)
			if err == nil && (customer != key.customer || amount != key.id%10000+1) {
				e.Outcome = "wrong"
			}
		} else {
			var name string
			err = db.QueryRowContext(ctx, "SELECT name FROM customers WHERE customer_id=$1", key.customer).Scan(&name)
			if err == nil && name != "customer-"+strconv.Itoa(key.customer) {
				e.Outcome = "wrong"
			}
		}
		cancel()
		e.At = time.Now().UTC()
		e.Latency = float64(time.Since(started)) / float64(time.Millisecond)
		if errors.Is(err, sql.ErrNoRows) {
			e.Outcome = "missing"
		} else if err != nil {
			e.Outcome = "error"
			e.Error = err.Error()
			if len(e.Error) > 1000 {
				e.Error = e.Error[:1000]
			}
		}
		if err = j.append(e); err != nil {
			return err
		}
		time.Sleep(100 * time.Millisecond)
	}
	return nil
}
