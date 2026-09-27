// The module trees/producer contains the code for a sample data producer

package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
)

func main() {
	conf, err := parseConfig(os.Args[1:])
	if errors.Is(err, flag.ErrHelp) {
		return
	} else if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	logger := common.InitLogger(conf.LogLevel)

	// a context that is cancelled on SIGINT/SIGTERM
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// a single client is shared by all the workers (kgo.Client is goroutine safe and batches records internally)
	client, err := newProducerClient(conf, logger)
	if err != nil {
		logger.Error("failed to create producer client", "err", err)
		os.Exit(1)
	}

	// make sure that the kafka topic exists (blocking call with a loop) in case the client spins up before kafka is up
	if err := common.WaitForTopics(ctx, client, logger, conf.Topic); err != nil {
		logger.Error("topic is not available", "topic", conf.Topic, "err", err)
		client.Close()
		os.Exit(1)
	}

	// start the workers
	var wg sync.WaitGroup
	for i := 0; i < conf.Workers; i++ {
		wg.Add(2) // one sync and one async producer loop per worker
		go func(id int) { defer wg.Done(); startSyncProducer(ctx, id, conf, client, logger) }(i)
		go func(id int) { defer wg.Done(); startAsyncProducer(ctx, id, conf, client, logger) }(i)
	}

	<-ctx.Done()
	logger.Info("Terminating: signal received, waiting for workers to shutdown")
	wg.Wait()

	// flush any buffered async records before closing the client
	flushCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Flush(flushCtx); err != nil {
		logger.Error("failed to flush records", "err", err)
	}
	client.Close()
	logger.Info("Shutdown successful")
}
