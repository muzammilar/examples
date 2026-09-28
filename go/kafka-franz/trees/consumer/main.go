// The module trees/consumer contains the code for a sample data consumer (consumer group member)

package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

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

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	client, err := newConsumerClient(conf, logger)
	if err != nil {
		logger.Error("failed to create consumer client", "err", err)
		os.Exit(1)
	}

	// wait for the topics, otherwise the group may start without partitions (they are picked up on metadata refresh anyway)
	if err := common.WaitForTopics(ctx, client, logger, conf.Topics...); err != nil {
		logger.Error("topics are not available", "topics", conf.Topics, "err", err)
		client.Close()
		os.Exit(1)
	}

	consume(ctx, client, logger)

	// Close leaves the group (triggering a rebalance for the other members) and commits the marked offsets
	logger.Info("Terminating: leaving the consumer group")
	client.Close()
	logger.Info("Shutdown successful")
}
