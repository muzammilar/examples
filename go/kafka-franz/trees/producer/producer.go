// The module producer contains the code for a sample data producer

package main

import (
	"context"
	"log/slog"
	"math/rand"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kgo"
)

/*
 * Sync Producer
 */

// startSyncProducer produces one record at a time and waits for the broker acknowledgement
func startSyncProducer(ctx context.Context, id int, conf *config, client *kgo.Client, logger *slog.Logger) {
	logger = logger.With("worker", id, "producer", "sync")
	randGen := rand.New(rand.NewSource(time.Now().UnixNano())) // rand.Rand is not thread safe
	ticker := time.NewTicker(conf.Interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			logger.Info("shut down")
			return
		case <-ticker.C:
		}

		record, err := common.NewRecord(conf.Topic, common.UserIDMin+randGen.Intn(common.UserIDRange), common.ProducerSync)
		if err != nil {
			logger.Warn("failed to serialize message", "err", err)
			continue
		}
		// ProduceSync blocks until the record is acknowledged (or fails)
		r, err := client.ProduceSync(ctx, record).First()
		if err != nil {
			if ctx.Err() == nil {
				logger.Error("failed to send message", "err", err)
			}
			continue
		}
		logger.Debug("saved a message", "partition", r.Partition, "offset", r.Offset)
	}
}

/*
 * Async Producer
 */

// startAsyncProducer buffers records in the client and receives the result via a promise (callback)
func startAsyncProducer(ctx context.Context, id int, conf *config, client *kgo.Client, logger *slog.Logger) {
	logger = logger.With("worker", id, "producer", "async")
	randGen := rand.New(rand.NewSource(time.Now().UnixNano()))
	ticker := time.NewTicker(conf.Interval)
	defer ticker.Stop()

	// the promise is called from a single client goroutine, so it must not block
	promise := func(r *kgo.Record, err error) {
		if err != nil {
			logger.Error("failed to send message", "err", err)
			return
		}
		logger.Debug("saved a message", "partition", r.Partition, "offset", r.Offset)
	}

	for {
		select {
		case <-ctx.Done():
			logger.Info("shut down (buffered records are flushed by main)")
			return
		case <-ticker.C:
		}

		record, err := common.NewRecord(conf.Topic, common.UserIDMin+randGen.Intn(common.UserIDRange), common.ProducerAsync)
		if err != nil {
			logger.Warn("failed to serialize message", "err", err)
			continue
		}
		// Use a background context, so that buffered records are not failed when ctx is cancelled (Flush in main)
		client.Produce(context.Background(), record, promise)
	}
}

/*
 * Client
 */

func newProducerClient(conf *config, logger *slog.Logger) (*kgo.Client, error) {
	return common.NewClient(conf.Brokers, "treeproducer", conf.Verbose,
		kgo.DefaultProduceTopic(conf.Topic),
		kgo.RecordPartitioner(conf.Partitioner),
		kgo.RequiredAcks(kgo.AllISRAcks()), // idempotent production (the default) requires acks from all in-sync replicas
		kgo.ProducerLinger(5*time.Millisecond),
		kgo.MetadataMaxAge(1*time.Minute),
		common.StartMetricsServer("treeproducer", conf.MetricsAddr, logger),
	)
}
