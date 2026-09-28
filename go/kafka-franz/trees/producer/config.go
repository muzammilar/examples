// The module trees/producer contains the code for a sample data producer

package main

import (
	"flag"
	"fmt"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kgo"
)

// config is the producer configuration
type config struct {
	Brokers     []string
	Topic       string
	Workers     int
	Partitioner kgo.Partitioner
	Interval    time.Duration
	MetricsAddr string
	Verbose     bool
	LogLevel    string
}

// parseConfig parses and validates the command line flags
func parseConfig(args []string) (*config, error) {
	fs := flag.NewFlagSet("treeproducer", flag.ContinueOnError)
	brokers := fs.String("brokers", common.DefaultKafkaBrokers, "Kafka bootstrap brokers to connect to, as a comma separated list")
	workers := fs.Int("workers", 2, "Number of producer workers (each runs a sync and an async producer loop)")
	topic := fs.String("topic", common.DefaultTopic, "Kafka topic to send data to")
	partitioner := fs.String("partitioner", common.DefaultPartitioner, fmt.Sprintf("Producer partition selection strategy. One of %+v", common.SupportedPartitioners))
	interval := fs.Duration("interval", common.DefaultMessageSendIntervalMs*time.Millisecond, "Interval between messages for each producer loop")
	metricsAddr := fs.String("metrics.addr", common.DefaultMetricsAddr, "Address to expose prometheus metrics on")
	verbose := fs.Bool("log.kgo", false, "Enable franz-go debug logging to stderr")
	loglevel := fs.String("log.level", common.DefaultLoggingLevel, "The logging level for the program (except franz-go logs).")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}

	c := &config{
		Brokers:     common.SplitList(*brokers),
		Topic:       *topic,
		Workers:     *workers,
		Interval:    *interval,
		MetricsAddr: *metricsAddr,
		Verbose:     *verbose,
		LogLevel:    *loglevel,
	}
	if err := common.ValidateBrokers(c.Brokers); err != nil {
		return nil, err
	}
	if c.Topic == "" {
		return nil, fmt.Errorf("a topic is required")
	}
	if c.Workers < 1 {
		return nil, fmt.Errorf("at least one worker is required, got %d", c.Workers)
	}
	if c.Interval <= 0 {
		return nil, fmt.Errorf("the interval must be positive, got %s", c.Interval)
	}
	var err error
	if c.Partitioner, err = common.ParsePartitioner(*partitioner); err != nil {
		return nil, err
	}
	return c, nil
}
