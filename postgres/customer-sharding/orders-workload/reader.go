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
	samples := make([][]sample, 3)
	// Immutable, previously reconciled ACKs are available on every physical copy.
	// On a fresh fixture the reader instead checks seeded customer bodies.
	for _, r := range l.runs {
		for offset, v := range r.flags {
			if v&acknowledged != 0 {
				c := (offset / r.config.Batch) % f.Customers
				s := route[c]
				if len(samples[s]) < 16 {
					samples[s] = append(samples[s], sample{r.config.Base + int64(offset), c, true})
				}
			}
		}
	}
	for s := 0; s < 3; s++ {
		if len(samples[s]) == 0 {
			for c, shard := range route {
				if s == shard {
					samples[s] = append(samples[s], sample{customer: c})
					break
				}
			}
		}
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
		shard := index % 3
		if len(samples[shard]) == 0 {
			continue
		}
		key := samples[shard][(index/3)%len(samples[shard])]
		e := event{Stage: "read", Started: time.Now().UTC(), First: key.id, Customer: key.customer, Shard: shard, Outcome: "ok"}
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
