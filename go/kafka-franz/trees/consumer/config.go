// The module trees/consumer contains the code for a sample data consumer (consumer group member)

package main

import (
	"flag"
	"fmt"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kgo"
)

// config is the consumer configuration
type config struct {
	Brokers     []string
	Group       string
	Topics      []string
	Balancer    kgo.GroupBalancer
	ResetOffset kgo.Offset
	MetricsAddr string
	Verbose     bool
	LogLevel    string
}

// parseConfig parses and validates the command line flags
func parseConfig(args []string) (*config, error) {
	fs := flag.NewFlagSet("treeconsumer", flag.ContinueOnError)
	brokers := fs.String("brokers", common.DefaultKafkaBrokers, "Kafka bootstrap brokers to connect to, as a comma separated list")
	group := fs.String("group", common.DefaultConsumerGroup, "Kafka consumer group name")
	topics := fs.String("topics", common.DefaultTopic, "Kafka topics to be consumed, as a comma separated list")
	balancer := fs.String("balancer", common.DefaultBalancer, fmt.Sprintf("Consumer group partition balancer. One of %+v", common.SupportedBalancers))
	oldest := fs.Bool("oldest", true, "Start from the oldest offset when the group has no committed offset (otherwise newest)")
	metricsAddr := fs.String("metrics.addr", common.DefaultMetricsAddr, "Address to expose prometheus metrics on")
	verbose := fs.Bool("log.kgo", false, "Enable franz-go debug logging to stderr")
	loglevel := fs.String("log.level", common.DefaultLoggingLevel, "The logging level for the program (except franz-go logs).")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}

	c := &config{
		Brokers:     common.SplitList(*brokers),
		Group:       *group,
		Topics:      common.SplitList(*topics),
		ResetOffset: common.ResetOffset(*oldest),
		MetricsAddr: *metricsAddr,
		Verbose:     *verbose,
		LogLevel:    *loglevel,
	}
	if err := common.ValidateBrokers(c.Brokers); err != nil {
		return nil, err
	}
	if c.Group == "" {
		return nil, fmt.Errorf("a consumer group is required")
	}
	if len(c.Topics) == 0 {
		return nil, fmt.Errorf("at least one topic is required")
	}
	var err error
	if c.Balancer, err = common.ParseBalancer(*balancer); err != nil {
		return nil, err
	}
	return c, nil
}
