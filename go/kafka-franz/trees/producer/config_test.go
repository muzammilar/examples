package main

import (
	"errors"
	"flag"
	"testing"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
)

func TestParseConfigDefaults(t *testing.T) {
	c, err := parseConfig(nil)
	if err != nil {
		t.Fatal(err)
	}
	if c.Topic != common.DefaultTopic || c.Workers != 2 || c.Partitioner == nil || c.MetricsAddr != common.DefaultMetricsAddr {
		t.Errorf("unexpected defaults: %+v", c)
	}
	if c.Interval != common.DefaultMessageSendIntervalMs*time.Millisecond {
		t.Errorf("unexpected interval %s", c.Interval)
	}
}

func TestParseConfigFlags(t *testing.T) {
	c, err := parseConfig([]string{"-brokers", "localhost:29092", "-topic", "test", "-workers", "5", "-partitioner", common.PartitionRoundRobin, "-interval", "1s"})
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Brokers) != 1 || c.Topic != "test" || c.Workers != 5 || c.Interval != time.Second {
		t.Errorf("unexpected config: %+v", c)
	}
}

func TestParseConfigInvalid(t *testing.T) {
	for _, args := range [][]string{
		{"-brokers", ","},
		{"-topic", ""},
		{"-workers", "0"},
		{"-interval", "0s"},
		{"-partitioner", "murmur"},
		{"-workers", "many"},
	} {
		if _, err := parseConfig(args); err == nil {
			t.Errorf("parseConfig(%v) expected an error", args)
		}
	}
	if _, err := parseConfig([]string{"-h"}); !errors.Is(err, flag.ErrHelp) {
		t.Errorf("parseConfig(-h) = %v, want flag.ErrHelp", err)
	}
}
