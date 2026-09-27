// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"context"
	"errors"
	"log/slog"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kerr"
	"github.com/twmb/franz-go/pkg/kgo"
)

// CreateTopicsIfNotExist creates the topics (with the given partitions and replication factor) that do not exist yet.
// Existing topics are left untouched.
// `configs` are optional topic configs (e.g. retention.ms), see ParseTopicConfigs.
func CreateTopicsIfNotExist(ctx context.Context, client *kgo.Client, partitions int32, replicationFactor int16, configs map[string]*string, logger *slog.Logger, topics ...string) error {
	adm := kadm.NewClient(client)

	details, err := adm.ListTopics(ctx, topics...)
	if err != nil {
		return err
	}

	var missing []string
	for _, topic := range topics {
		if details.Has(topic) {
			logger.Info("topic already exists", "topic", topic, "partitions", len(details[topic].Partitions))
			continue
		}
		missing = append(missing, topic)
	}
	if len(missing) == 0 {
		return nil
	}

	resps, err := adm.CreateTopics(ctx, partitions, replicationFactor, configs, missing...)
	if err != nil {
		return err
	}
	var errs []error
	for _, r := range resps.Sorted() {
		switch {
		case errors.Is(r.Err, kerr.TopicAlreadyExists): // someone else created it in the meantime
			logger.Info("topic already exists", "topic", r.Topic)
		case r.Err != nil:
			errs = append(errs, r.Err)
			logger.Error("failed to create topic", "topic", r.Topic, "err", r.Err, "message", r.ErrMessage)
		default:
			logger.Info("created topic", "topic", r.Topic, "partitions", r.NumPartitions, "replication", r.ReplicationFactor)
		}
	}
	return errors.Join(errs...)
}

// WaitForTopics blocks until all the topics exist with their partitions (e.g. the producer/consumer started before kafka or the admin job)
func WaitForTopics(ctx context.Context, client *kgo.Client, logger *slog.Logger, topics ...string) error {
	adm := kadm.NewClient(client)
	for {
		details, err := adm.ListTopics(ctx, topics...)
		if err == nil {
			ready := true
			for _, t := range topics {
				// a newly created topic can exist without partition leaders for a moment
				if d, ok := details[t]; !ok || d.Err != nil || len(d.Partitions) == 0 {
					ready = false
				}
			}
			if ready {
				return nil
			}
		}
		logger.Warn("waiting for topics to exist", "topics", topics, "err", err)
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(DefaultConnectionBackoffMs * time.Millisecond):
		}
	}
}
