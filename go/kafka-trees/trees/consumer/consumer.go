// The module trees/consumer contains the code for a sample data consumer

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/Shopify/sarama"
	"github.com/muzammilar/examples-go/kafka-trees/trees/common"
	"github.com/sirupsen/logrus"
)

/*
 * ConsumerGroupHandler Interface
 */

type ConsumerHandler struct {
	logger *logrus.Logger
}

// Setup is run at the beginning of a new session, before ConsumeClaim.
func (c *ConsumerHandler) Setup(cgs sarama.ConsumerGroupSession) error {
	c.logger.Infof("ConsumerHandler - Starting session with ID (%s-%d) for claims: %+v", cgs.MemberID(), cgs.GenerationID(), cgs.Claims())
	return nil
}

// Cleanup is run at the end of a session, once all ConsumeClaim goroutines have exited
// but before the offsets are committed for the very last time.
func (c *ConsumerHandler) Cleanup(cgs sarama.ConsumerGroupSession) error {
	c.logger.Infof("ConsumerHandler - Cleaning up session with ID (%s-%d) for claims: %+v", cgs.MemberID(), cgs.GenerationID(), cgs.Claims())
	return nil
}

// ConsumeClaim must start a consumer loop of ConsumerGroupClaim's Messages().
// Once the Messages() channel is closed, the Handler must finish its processing
// loop and exit.
func (c *ConsumerHandler) ConsumeClaim(cgs sarama.ConsumerGroupSession, claim sarama.ConsumerGroupClaim) error {
	// Note:  The `ConsumeClaim` itself is called within a goroutine (so doesn't need another routine)
	var data map[string]interface{}

	for message := range claim.Messages() {
		// parse the message
		err := json.Unmarshal(message.Value, &data)
		if err != nil {
			c.logger.Warnf("ConsumerHandler - Topic:%q Partition:%d Offset:%d - Failed to read convert message to JSON: %s\n", message.Topic, message.Partition, message.Offset, string(message.Value))
		}
		// log the message
		c.logger.Debugf("ConsumerHandler - Topic:%q Partition:%d Offset:%d - Recieved message: %v\n", message.Topic, message.Partition, message.Offset, data)
		// mark the message as read
		cgs.MarkMessage(message, "")
	}
	return nil
}

/*
 * Configs
 */
func consumerConfig(logger *logrus.Logger) *sarama.Config {
	config := sarama.NewConfig()
	// version options
	version, err := sarama.ParseKafkaVersion(versionStr)
	if err != nil {
		panic(fmt.Sprintf("Error parsing Kafka version: %v", err))
	}
	config.Version = version
	// add logging support in verbose mode
	if verbose {
		sarama.Logger = logger
	}

	// select assigner
	switch assigner {
	case sarama.StickyBalanceStrategyName:
		config.Consumer.Group.Rebalance.Strategy = sarama.BalanceStrategySticky
	case sarama.RoundRobinBalanceStrategyName:
		config.Consumer.Group.Rebalance.Strategy = sarama.BalanceStrategyRoundRobin
	case sarama.RangeBalanceStrategyName:
		config.Consumer.Group.Rebalance.Strategy = sarama.BalanceStrategyRange
	default:
		log.Panicf("Unrecognized consumer group partition assignor: %s", assigner)
	}

	// MetaData Refresh Frequency. Generally, it shold be high since it doesn't change often. But for PoC we start producer before kafka often
	config.Metadata.RefreshFrequency = 1 * time.Minute

	// Consumer Offset (defaults to newest)
	if oldest {
		config.Consumer.Offsets.Initial = sarama.OffsetOldest
	}
	config.ClientID = fmt.Sprintf("tree-%s-client", common.Hostname()) // the id of the client. It should generally have a hostname as well
	config.Consumer.Return.Errors = true                               // return the errors via a channel to the user

	// Metrics
	config.MetricRegistry = common.MetricsRegistry // alternatively we can use metrics.DefaultRegistry

	return config
}

/*
 * Consumer Main
 */

func startConsumer(wg *sync.WaitGroup, ctx context.Context, brokers []string, topics []string, logger *logrus.Logger) {
	// wait group
	defer wg.Done()

	// create a consumer group
	conf := consumerConfig(logger)
	consumer, err := sarama.NewConsumerGroup(brokers, group, conf)
	if err != nil {
		logger.Fatalf("Consumer - Error creating kafka consumer group client: %v", err)
	}

	//
	// read from the error channel
	go func() {
		for err := range consumer.Errors() {
			logger.Errorf("Consumer - Error occured: %s", err.Error())
		}
		logger.Info("Consumer - error channel closed")
	}()

	// create a new consumer handler
	handler := &ConsumerHandler{
		logger: logger,
	}

	// Iterate over sarama.ConsumerGroupSession and call Claim (blocking loop)
	// `Consume` should be called inside a loop.
	// When server-side rebalance happens, the consumer session will need to be
	// recreated to get the new claims
	var consuming bool = true
	for consuming {
		// start the consuming session
		if err = consumer.Consume(ctx, topics, handler); err != nil {
			log.Fatalf("Consumer - Consume Error: %v", err)
		}
		// check if context was cancelled, signaling that the consumer should stop
		select {
		case <-ctx.Done():
			consuming = false
		default: // a default case to make this non blocking select
			// sleep for a while before retrying (since the kafka cluster might have gone down)
			// e.g err == sarama.ErrOutOfBrokers (err above)
		}
	}
	// ignore wait for error channel to close - not really needed

	// close the consumer client and record errors if any
	if err = consumer.Close(); err != nil {
		log.Panicf("Consumer - Error closing client: %v", err)
	}

}
