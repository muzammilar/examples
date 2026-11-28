// Produce bounded, deterministic batches; preserve every ACK outside Kafka.
package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"examples/orders-lakehouse/internal/observe"
	"examples/orders-lakehouse/internal/orders"
	"fmt"
	"github.com/twmb/franz-go/pkg/kgo"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

type ack struct {
	ID        int64 `json:"id"`
	Partition int32 `json:"partition"`
	Offset    int64 `json:"offset"`
}

func emit(v any) {
	if err := json.NewEncoder(os.Stdout).Encode(v); err != nil {
		panic(err)
	}
}
func number(value string) (int64, error) {
	v, e := strconv.ParseInt(value, 10, 64)
	if e != nil || v < 1 {
		return 0, fmt.Errorf("positive integer required: %q", value)
	}
	return v, nil
}
func journal(file *os.File, value any) error {
	if e := json.NewEncoder(file).Encode(value); e != nil {
		return e
	}
	return file.Sync()
}
func run() error {
	if len(os.Args) == 2 && os.Args[1] == "cgroup" {
		observe.Raw()
		return nil
	}
	defer observe.Footer()
	if len(os.Args) != 4 {
		return fmt.Errorf("producer <baseline|live|maintenance> <first-id> <count>")
	}
	phase := os.Args[1]
	if phase != "baseline" && phase != "live" && phase != "maintenance" {
		return fmt.Errorf("invalid phase")
	}
	first, e := number(os.Args[2])
	if e != nil {
		return e
	}
	count, e := number(os.Args[3])
	if e != nil {
		return e
	}
	customers, e := number(os.Getenv("CUSTOMER_COUNT"))
	if e != nil {
		return e
	}
	rate, e := number(os.Getenv("INSERT_RATE"))
	if e != nil {
		return e
	}
	total, e := number(os.Getenv("ORDER_COUNT"))
	if e != nil {
		return e
	}
	if first+count-1 > total || total > 10000000 || customers > total {
		return fmt.Errorf("range outside declared corpus")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	client, e := kgo.NewClient(kgo.SeedBrokers("broker1:9092", "broker2:9092", "broker3:9092"), kgo.ClientID("lake-orders-"+phase), kgo.RecordPartitioner(kgo.ManualPartitioner()), kgo.RequiredAcks(kgo.AllISRAcks()), kgo.MaxProduceRequestsInflightPerBroker(1), kgo.ProducerBatchCompression(kgo.ZstdCompression()))
	if e != nil {
		return e
	}
	defer client.Close()
	emit(map[string]any{"client_config": map[string]any{"driver": "franz-go 1.22.3", "acks": "all", "min_isr": 2, "idempotence": "native default enabled", "inflight_per_broker": 1, "compression": "zstd", "batch_records": 1000, "target_records_s": rate, "arrival": "paced closed loop", "deadline_s": 600, "mutation_retry": "native same-session idempotent retries only"}})
	file, e := os.OpenFile(filepath.Join("/witness", phase+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return fmt.Errorf("refuse to overwrite producer witness: %w", e)
	}
	defer file.Close()
	started := time.Now()
	var completed int64
	for completed < count {
		n := int64(1000)
		if count-completed < n {
			n = count - completed
		}
		records := make([]*kgo.Record, 0, n)
		ids := make(map[*kgo.Record]int64, n)
		digest := sha256.New()
		for i := int64(0); i < n; i++ {
			id := first + completed + i
			customer := 1 + (id-1)%customers
			o := orders.Order{Phase: phase, ID: id, Customer: customer, Amount: 100 + id%1000, Region: fmt.Sprintf("region-%d", customer%16), Revision: 1, CustomerRevision: 1, EventSecond: 1700000000 + id%86400}
			body, e := json.Marshal(o)
			if e != nil {
				return e
			}
			digest.Write(body)
			r := &kgo.Record{Topic: "lake-orders", Partition: int32(customer % 3), Key: []byte(strconv.FormatInt(customer, 10)), Value: body}
			records = append(records, r)
			ids[r] = id
		}
		batchStart := time.Now()
		if e = journal(file, map[string]any{"kind": "intent", "phase": phase, "first": first + completed, "count": n, "ts_start": batchStart.UTC().Format(time.RFC3339Nano), "body_sha256": hex.EncodeToString(digest.Sum(nil))}); e != nil {
			return e
		}
		results := client.ProduceSync(ctx, records...)
		acks := make([]ack, 0, n)
		unknown := []map[string]any{}
		for _, r := range results {
			if r.Err != nil {
				unknown = append(unknown, map[string]any{"id": ids[r.Record], "error": r.Err.Error()})
			} else {
				acks = append(acks, ack{ID: ids[r.Record], Partition: r.Record.Partition, Offset: r.Record.Offset})
			}
		}
		if e = journal(file, map[string]any{"kind": "result", "phase": phase, "first": first + completed, "count": n, "ts_start": batchStart.UTC().Format(time.RFC3339Nano), "ts_end": time.Now().UTC().Format(time.RFC3339Nano), "acks": acks, "unknown": unknown, "wall_ms": float64(time.Since(batchStart).Nanoseconds()) / 1e6}); e != nil {
			return e
		}
		if len(unknown) > 0 || len(acks) != int(n) {
			return fmt.Errorf("producer ambiguity preserved; reconcile before any retry")
		}
		completed += n
		emit(map[string]any{"phase": phase, "acknowledged": completed, "wall_s": time.Since(started).Seconds(), "last_batch_ms": float64(time.Since(batchStart).Nanoseconds()) / 1e6})
		due := started.Add(time.Duration(float64(completed) / float64(rate) * float64(time.Second)))
		if delay := time.Until(due); delay > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(delay):
			}
		}
	}
	emit(map[string]any{"passed": true, "phase": phase, "rows": completed, "wall_s": time.Since(started).Seconds()})
	return nil
}
func main() {
	if e := run(); e != nil {
		fmt.Fprintln(os.Stderr, e)
		os.Exit(1)
	}
}
