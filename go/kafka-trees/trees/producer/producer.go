// The module producer contains the code for a sample data producer

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand"
	"strconv"
	"sync"
	"time"

	"github.com/muzammilar/examples-go/kafka-trees/trees/common"

	"github.com/Shopify/sarama"
	"github.com/sirupsen/logrus"
)

/*
 * Message
 */

type Message struct {
	Async  int    // Whether the data is sent as sync or async
	UserId int    // This ID is NOT unique between messages and can be repeated
	Data   string // Random tree name
}

func prepareMessage(topic string, userId int, async int, logger *logrus.Logger) *sarama.ProducerMessage {
	msg := Message{
		Async:  async,
		UserId: userId,
		Data:   common.Trees[userId%len(common.Trees)], // len should be O(1) https://pkg.go.dev/reflect?utm_source=godoc#SliceHeader
	}

	// Note: JSON serialization is approximately 5-10x slower than binary formats (like protobufs) in my tests
	jmsg, err := json.Marshal(msg)
	if err != nil {
		logger.Warnf("Failed to serialize '%#v' to JSON. Error: %s", msg, err.Error())
	}

	pmsg := &sarama.ProducerMessage{
		Topic: topic,
		Key:   sarama.StringEncoder(strconv.Itoa(userId)), // Partitioning Key
		Value: sarama.ByteEncoder(jmsg),
	}

	return pmsg
}

/*
 * Sync Producer
 */

func startSyncProducer(wg *sync.WaitGroup, ctx context.Context, id int, syncproducer sarama.SyncProducer, logger *logrus.Logger) {
	// wait group cleanup
	defer wg.Done()

	// create a new random generator source
	randSouce := rand.NewSource(time.Now().UnixNano()) // NewSource is not thread safe
	randGen := rand.New(randSouce)

producerLoop:
	for {
		// sleep for a while
		time.Sleep(common.DefaultMessageSendIntervalMs * time.Millisecond)

		// get a random user id
		userId := common.UserIDMin + randGen.Intn(common.UserIDRange)

		// check context
		select {
		case <-ctx.Done():
			if err := syncproducer.Close(); err != nil {
				logger.Errorf("Worker #%d - syncproducer - failed to close with error: '%#v'", id, err)
			}
			break producerLoop
		default:
			// A `default` case is needed to make this select non-blocking
		}
		// send message
		msg := prepareMessage(topic, userId, common.ProducerSync, logger)
		partition, offset, err := syncproducer.SendMessage(msg)
		if err != nil {
			logger.Errorf("Worker #%d -  syncproducer - failed to send message with error: %s", id, err.Error())
		} else {
			logger.Debugf("Worker #%d -  syncproducer - saved a message to partion %d with offset %d", id, partition, offset)
		}
	}
	logger.Infof("Worker #%d - syncproducer - shut down", id)
}

func newSyncProducer(brokers []string, id int, logger *logrus.Logger) sarama.SyncProducer {
	var producer sarama.SyncProducer
	var err error
	// get configs
	config := producerConfig(logger)
	// create producer with retry logic
	// ignore context handling here (can cause deadlock)
	for producer, err = sarama.NewSyncProducer(brokers, config); err != nil; producer, err = sarama.NewSyncProducer(brokers, config) {
		logger.Errorf("Worker #%d - failed to create a sync producer for broker '%+v': %#v", id, brokers, err)
		time.Sleep(common.DefaultConnectionBackoffMs * time.Millisecond)
	}

	return producer
}

/*
 * Async Producer
 */

func startAsyncProducer(wg *sync.WaitGroup, ctx context.Context, id int, asyncproducer sarama.AsyncProducer, logger *logrus.Logger) {
	// wait group notification
	defer wg.Done()

	// create a new random generator source
	randSouce := rand.NewSource(time.Now().UnixNano()) // NewSource is not thread safe
	randGen := rand.New(randSouce)

	wg.Add(2) // one for the error channel and one for the succes channel
	// read from the error channel
	go func() {
		defer wg.Done()
		for err := range asyncproducer.Errors() {
			logger.Errorf("Worker #%d - asyncproducer - error: %s", id, err.Error())
		}
		logger.Infof("Worker #%d - asyncproducer - error channel closed", id)
	}()

	// read from the success channel (since we enabled this in configs)
	go func() {
		defer wg.Done()
		for msg := range asyncproducer.Successes() {
			logger.Debugf("Worker #%d -  asyncproducer - saved a message to partion %d with offset %d", id, msg.Partition, msg.Offset)
		}
		logger.Infof("Worker #%d - asyncproducer - success channel closed", id)
	}()

producerLoop:
	for {
		// sleep for a while
		time.Sleep(common.DefaultMessageSendIntervalMs * time.Millisecond)
		// get a random user id
		userId := common.UserIDMin + randGen.Intn(common.UserIDRange)
		// make a message
		msg := prepareMessage(topic, userId, common.ProducerAsync, logger)

		// check context
		select { // Do not write a default close otherwise it will become non-blocking
		case <-ctx.Done():
			asyncproducer.AsyncClose() // you can also use async close.
			break producerLoop         // see this to see how to close channels in select https://stackoverflow.com/questions/13666253/breaking-out-of-a-select-statement-when-all-channels-are-closed
		case asyncproducer.Input() <- msg:
			logger.Debugf("Worker #%d -  asyncproducer - created a message", id)
		}
	}
	logger.Infof("Worker #%d - asyncproducer - async shutdown initiated", id)
}

func newAsyncProducer(brokers []string, id int, logger *logrus.Logger) sarama.AsyncProducer {
	var producer sarama.AsyncProducer
	var err error
	// get configs
	config := producerConfig(logger)
	// create producer with retry logic
	// ignore context handling here (can cause deadlock)
	for producer, err = sarama.NewAsyncProducer(brokers, config); err != nil; producer, err = sarama.NewAsyncProducer(brokers, config) {
		logger.Errorf("Worker #%d - failed to create an async producer for broker '%+v': %#v", id, brokers, err)
		time.Sleep(common.DefaultConnectionBackoffMs * time.Millisecond)
	}

	return producer
}

/*
 * Configs
 */

func producerConfig(logger *logrus.Logger) *sarama.Config {
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
	// select partitioner
	switch partitioner {
	case common.PartitionHash:
		config.Producer.Partitioner = sarama.NewHashPartitioner
	case common.PartitionRand:
		config.Producer.Partitioner = sarama.NewRandomPartitioner
	case common.PartitionRoundRobin:
		config.Producer.Partitioner = sarama.NewRoundRobinPartitioner
	default: // panic should generally be avoided in production, however it's a config check early in the program so it should be okay
		panic(fmt.Sprintf("Unknown Kafka partitoner: %s", partitioner))
	}
	// MetaData Refresh Frequency. Generally, it shold be high since it doesn't change often. But for PoC we start producer before kafka often
	config.Metadata.RefreshFrequency = 1 * time.Minute
	// Kafka Acks
	config.Producer.RequiredAcks = sarama.WaitForLocal // wait for local commit to succeed
	//config.Producer.RequiredAcks = sarama.WaitForAll // wait for local commit to succeed
	config.Producer.Return.Errors = true    // return the errors via a channel to the user
	config.Producer.Return.Successes = true // It must always be true for sync producer, for async producer, it needs a channel read

	config.MetricRegistry = common.MetricsRegistry // alternatively we can use metrics.DefaultRegistry

	return config
}

/*
 * Producer Main
 */

func startProducer(wg *sync.WaitGroup, ctx context.Context, id int, brokers []string, logger *logrus.Logger) {

	// wait group
	defer wg.Done()

	var asyncproducer sarama.AsyncProducer
	var syncproducer sarama.SyncProducer
	// create the producers
	asyncproducer = newAsyncProducer(brokers, id, logger)
	syncproducer = newSyncProducer(brokers, id, logger)

	// start the producers
	var producerWg *sync.WaitGroup = new(sync.WaitGroup)
	producerWg.Add(2) // one for the sync producer and another for the async producer
	// sync producer
	go startSyncProducer(producerWg, ctx, id, syncproducer, logger)
	// async producer
	go startAsyncProducer(producerWg, ctx, id, asyncproducer, logger)

	// close the producer when the context is done
	<-ctx.Done()
	// wait for producers to cleanly shut down
	logger.Infof("Worker #%d - waiting for producers to shutdown", id)
	producerWg.Wait()
}
