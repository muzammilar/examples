// The module trees/consumer contains the code for a sample data consumer

package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/muzammilar/examples-go/kafka-trees/trees/common"
)

// Consumer configuration options
var (
	brokersStr = ""
	versionStr = ""
	group      = ""
	topicsStr  = ""
	assigner   = ""
	oldest     = true
	verbose    = false
	loglevel   = ""
)

func init() {
	flag.StringVar(&brokersStr, "brokers", common.DefaultKafkaBrokers, "Kafka bootstrap brokers to connect to, as a comma separated list")
	flag.StringVar(&group, "group", common.DefaultConsumerGroup, "Kafka consumer group definition")
	flag.StringVar(&versionStr, "version", common.DefaultKafkaVersion, "Kafka cluster version")
	flag.StringVar(&topicsStr, "topics", common.DefaultTopic, "Kafka topics to be consumed, as a comma separated list")
	flag.StringVar(&assigner, "assigner", common.DefaultAssigner, "Consumer group partition assignment strategy (range, roundrobin, sticky)")
	flag.BoolVar(&oldest, "oldest", true, "Kafka consumer consume initial offset from oldest")
	flag.BoolVar(&verbose, "log.sarama", false, "Enable Sarama logging to console (as Printf)")
	flag.StringVar(&loglevel, "log.level", common.DefaultLoggingLevel, "The logging level for the program (except sarama logs).")
	flag.Parse()
}

func main() {
	// setup logger
	logger := common.InitLoggerWithStdOut(loglevel)

	// convert strings to lists
	brokers := strings.Split(brokersStr, ",")
	topics := strings.Split(topicsStr, ",")

	// start the metrics handler (starts go routines)
	common.StartMetricsCollector("consumer")

	// validate brokers and topics and other input configs.
	// make sure that the kafka topic exists (blocking call with a loop) in case the client spins up before kafka is up
	conf := consumerConfig(logger)
	if err := conf.Validate(); err != nil {
		logger.Fatal(err)
	}
	for _, topic := range topics { // validate information for each topic
		for !common.ValidateTopicInformation(brokers, topic, conf, logger) {
			// retry interval
			time.Sleep(common.DefaultConnectionBackoffMs * time.Millisecond)
		}
	}

	// create a context
	ctx, cancel := context.WithCancel(context.Background())

	// setup the consumer and start it
	var wg *sync.WaitGroup = new(sync.WaitGroup)
	wg.Add(1)
	go startConsumer(wg, ctx, brokers, topics, logger)

	// start the signal handler with context
	sigterm := make(chan os.Signal, 1)
	signal.Notify(sigterm, syscall.SIGINT, syscall.SIGTERM)
	select { // Do not specify a `default` case for this select, since a `default` case makes the code non-blocking (and requires a loop)
	case <-ctx.Done():
		logger.Info("Terminating: context cancelled")
	case sig := <-sigterm:
		logger.Infof("Terminating: via signal %s", sig)
	}
	// cancel the context on signal
	cancel()

	// wait for the consumer to finish
	logger.Info("Waiting for the consumer to shutdown")
	wg.Wait()
	logger.Info("Shutdown successful")

}
