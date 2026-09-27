// The module trees/admin creates the kafka topics used by the producers and consumers

package main

import (
	"flag"
	"fmt"
	"math"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
)

// config is the admin configuration
type config struct {
	Brokers           []string
	Topics            []string
	Partitions        int32
	ReplicationFactor int16
	TopicConfigs      map[string]*string
	Timeout           time.Duration
	Verbose           bool
	LogLevel          string
}

// parseConfig parses and validates the command line flags
func parseConfig(args []string) (*config, error) {
	fs := flag.NewFlagSet("treeadmin", flag.ContinueOnError)
	brokers := fs.String("brokers", common.DefaultKafkaBrokers, "Kafka bootstrap brokers to connect to, as a comma separated list")
	topics := fs.String("topics", common.DefaultTopic, "Kafka topics to create, as a comma separated list")
	partitions := fs.Int("partitions", common.DefaultPartitions, "Number of partitions per topic (-1 for broker default)")
	replication := fs.Int("replication", common.DefaultReplicationFactor, "Replication factor per topic (-1 for broker default)")
	topicConfigs := fs.String("configs", "", "Topic configs as comma separated key=value pairs (e.g. retention.ms=3600000)")
	timeout := fs.Duration("timeout", 2*time.Minute, "Give up if the topics cannot be created in this duration")
	verbose := fs.Bool("log.kgo", false, "Enable franz-go debug logging to stderr")
	loglevel := fs.String("log.level", common.DefaultLoggingLevel, "The logging level for the program (except franz-go logs).")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}

	c := &config{
		Brokers:  common.SplitList(*brokers),
		Topics:   common.SplitList(*topics),
		Timeout:  *timeout,
		Verbose:  *verbose,
		LogLevel: *loglevel,
	}
	if err := common.ValidateBrokers(c.Brokers); err != nil {
		return nil, err
	}
	if len(c.Topics) == 0 {
		return nil, fmt.Errorf("at least one topic is required")
	}
	if *partitions == 0 || *partitions < -1 || *partitions > math.MaxInt32 {
		return nil, fmt.Errorf("invalid number of partitions %d (use -1 for the broker default)", *partitions)
	}
	if *replication == 0 || *replication < -1 || *replication > math.MaxInt16 {
		return nil, fmt.Errorf("invalid replication factor %d (use -1 for the broker default)", *replication)
	}
	c.Partitions, c.ReplicationFactor = int32(*partitions), int16(*replication)

	var err error
	if c.TopicConfigs, err = common.ParseTopicConfigs(*topicConfigs); err != nil {
		return nil, err
	}
	return c, nil
}
