// The module trees/admin creates the kafka topics used by the producers and consumers

package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
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

	client, err := common.NewClient(conf.Brokers, "treeadmin", conf.Verbose)
	if err != nil {
		logger.Error("failed to create client", "err", err)
		os.Exit(1)
	}
	defer client.Close()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, conf.Timeout)
	defer cancel()

	// retry in case the admin starts before kafka is ready
	for {
		err = common.CreateTopicsIfNotExist(ctx, client, conf.Partitions, conf.ReplicationFactor, conf.TopicConfigs, logger, conf.Topics...)
		if err == nil {
			logger.Info("topics are ready", "topics", conf.Topics)
			return
		}
		logger.Warn("failed to create topics, retrying", "err", err)
		select {
		case <-ctx.Done():
			logger.Error("giving up", "err", ctx.Err())
			client.Close()
			os.Exit(1)
		case <-time.After(common.DefaultConnectionBackoffMs * time.Millisecond):
		}
	}
}
