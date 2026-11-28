package main

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/twmb/franz-go/pkg/kgo"
)

func producer(id string) (*kgo.Client, error) {
	return kgo.NewClient(kgo.SeedBrokers("broker1:9092", "broker2:9092", "broker3:9092"), kgo.TransactionalID(id), kgo.RequiredAcks(kgo.AllISRAcks()), kgo.RecordPartitioner(kgo.ManualPartitioner()), kgo.RequestTimeoutOverhead(5*time.Second), kgo.ProducerBatchCompression(kgo.Lz4Compression()))
}

func publishFence(ctx context.Context) error {
	id, err := runID()
	if err != nil {
		return err
	}
	client, err := producer("mysql-margins-fence-" + id)
	if err != nil {
		return err
	}
	defer client.Close()
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if err = client.BeginTransaction(); err != nil {
		return err
	}
	data, _ := json.Marshal(map[string]string{"op": "barrier", "id": id})
	var records []*kgo.Record
	for partition := int32(0); partition < 4; partition++ {
		records = append(records, &kgo.Record{Topic: "margins.enriched", Partition: partition, Value: data})
	}
	if err = client.ProduceSync(ctx, records...).FirstErr(); err != nil {
		return err
	}
	if err = client.EndTransaction(ctx, kgo.TryCommit); err != nil {
		return fmt.Errorf("fence commit ambiguous: %w", err)
	}
	for _, record := range records {
		emit(map[string]any{"event": "fence", "id": id, "partition": record.Partition, "offset": record.Offset})
	}
	return nil
}

func probeIsolation(ctx context.Context) error {
	id, err := runID()
	if err != nil {
		return err
	}
	root, err := casePath()
	if err != nil {
		return err
	}
	client, err := producer("mysql-margins-isolation-" + id)
	if err != nil {
		return err
	}
	defer client.Close()
	if err = client.BeginTransaction(); err != nil {
		return err
	}
	uncommitted, _ := json.Marshal(map[string]string{"id": id, "state": "must-not-be-visible"})
	if err = client.ProduceSync(ctx, &kgo.Record{Topic: "margins.isolation", Partition: 0, Value: uncommitted}).FirstErr(); err != nil {
		return err
	}
	emit(map[string]any{"event": "probe-open", "id": id})
	deadline := time.NewTimer(30 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	for !exists(root + "/abort-probe") {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-deadline.C:
			return fmt.Errorf("controller did not release the isolation probe")
		case <-ticker.C:
		}
	}
	if err = client.EndTransaction(ctx, kgo.TryAbort); err != nil {
		return err
	}
	if err = client.BeginTransaction(); err != nil {
		return err
	}
	committed, _ := json.Marshal(map[string]string{"id": id, "state": "committed"})
	if err = client.ProduceSync(ctx, &kgo.Record{Topic: "margins.isolation", Partition: 0, Value: committed}).FirstErr(); err != nil {
		return err
	}
	if err = client.EndTransaction(ctx, kgo.TryCommit); err != nil {
		return err
	}
	emit(map[string]any{"event": "probe-committed", "id": id})
	return nil
}
