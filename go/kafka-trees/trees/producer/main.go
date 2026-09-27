// The module trees/producer contains the code for a sample data producer

package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/muzammilar/examples-go/kafka-trees/trees/common"
)

// Producer configuration options
var (
	brokersStr  = ""
	versionStr  = ""
	topic       = ""
	partitioner = ""
	verbose     = false
	workers     = 1
	loglevel    = ""
)

func init() {

	// program flags
	flag.StringVar(&brokersStr, "brokers", common.DefaultKafkaBrokers, "Kafka bootstrap brokers to connect to, as a comma separated list")
	flag.IntVar(&workers, "workers", 2, "Number of producers for the program")
	flag.StringVar(&versionStr, "version", common.DefaultKafkaVersion, "Kafka cluster version")
	flag.StringVar(&topic, "topic", common.DefaultTopic, "Kafka topic to send data to")
	flag.StringVar(&partitioner, "partitioner", common.DefaultPartitioner, fmt.Sprintf("Producer partition selection strategy. Currently only supports %+v", common.SupportedPartitioners))
	flag.BoolVar(&verbose, "log.sarama", false, "Enable Sarama logging to console (as Printf)")
	flag.StringVar(&loglevel, "log.level", common.DefaultLoggingLevel, "The logging level for the program (except sarama logs).")
	flag.Parse()

}

func main() {

	// setup logger
	logger := common.InitLoggerWithStdOut(loglevel)

	// convert strings to lists
	brokers := strings.Split(brokersStr, ",")

	// start the metrics handler (starts go routines)
	common.StartMetricsCollector("producer")

	// validate brokers and topics and other input configs.
	// make sure that the kafka topic exists (blocking call with a loop) in case the client spins up before kafka is up
	conf := producerConfig(logger)
	if err := conf.Validate(); err != nil {
		logger.Fatal(err)
	}
	for !common.ValidateTopicInformation(brokers, topic, conf, logger) {
		// retry interval
		time.Sleep(common.DefaultConnectionBackoffMs * time.Millisecond)
	}

	// create a context
	ctx, cancel := context.WithCancel(context.Background())

	// setup the workers
	var wg *sync.WaitGroup = new(sync.WaitGroup)
	wg.Add(workers)

	// start the workers
	for i := 0; i < workers; i++ {
		go startProducer(wg, ctx, i, brokers, logger)
	}

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

	// wait for the workers to finish
	logger.Info("Waiting for workers to shutdown")
	wg.Wait()
	logger.Info("Shutdown successful")
}
