// The module trees/consumer contains the code for a sample data consumer (consumer group member)

package main

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kgo"
)

/*
 * Consumer Loop
 */

// consume polls records until the context is cancelled. Records are marked after processing and the
// marked offsets are committed periodically (AutoCommitMarks), i.e. at-least-once processing.
func consume(ctx context.Context, client *kgo.Client, logger *slog.Logger) {
	for {
		fetches := client.PollFetches(ctx)
		if fetches.IsClientClosed() || ctx.Err() != nil {
			return
		}
		fetches.EachError(func(topic string, partition int32, err error) {
			if !errors.Is(err, context.Canceled) {
				logger.Error("fetch error", "topic", topic, "partition", partition, "err", err)
			}
		})
		fetches.EachRecord(func(r *kgo.Record) {
			msg, err := common.DecodeMessage(r.Value)
			if err != nil {
				logger.Warn("failed to parse message", "topic", r.Topic, "partition", r.Partition, "offset", r.Offset, "value", string(r.Value))
			} else {
				logger.Debug("received message", "topic", r.Topic, "partition", r.Partition, "offset", r.Offset, "message", msg)
			}
			// mark the record as processed, so that its offset is committed
			client.MarkCommitRecords(r)
		})
		logger.Debug("processed fetch", "records", fetches.NumRecords())
	}
}

/*
 * Client
 */

func newConsumerClient(conf *config, logger *slog.Logger) (*kgo.Client, error) {
	return common.NewClient(conf.Brokers, "treeconsumer", conf.Verbose,
		kgo.ConsumerGroup(conf.Group),
		kgo.ConsumeTopics(conf.Topics...),
		kgo.Balancers(conf.Balancer),
		kgo.ConsumeResetOffset(conf.ResetOffset),
		kgo.AutoCommitMarks(), // only commit records that were marked via MarkCommitRecords
		kgo.AutoCommitInterval(1*time.Second),
		kgo.MetadataMaxAge(1*time.Minute),
		// group lifecycle callbacks (equivalent of sarama's Setup/Cleanup). With the cooperative balancer
		// they are called with empty maps during incremental rebalances, so those are skipped
		kgo.OnPartitionsAssigned(func(_ context.Context, _ *kgo.Client, assigned map[string][]int32) {
			if len(assigned) > 0 {
				logger.Info("partitions assigned", "assigned", assigned)
			}
		}),
		kgo.OnPartitionsRevoked(func(ctx context.Context, cl *kgo.Client, revoked map[string][]int32) {
			if len(revoked) == 0 {
				return
			}
			logger.Info("partitions revoked", "revoked", revoked)
			// commit what has been processed before the partitions move to another member
			if err := cl.CommitMarkedOffsets(ctx); err != nil {
				logger.Error("failed to commit offsets on revoke", "err", err)
			}
		}),
		kgo.OnPartitionsLost(func(_ context.Context, _ *kgo.Client, lost map[string][]int32) {
			logger.Warn("partitions lost", "lost", lost)
		}),
		common.StartMetricsServer("treeconsumer", conf.MetricsAddr, logger),
	)
}
