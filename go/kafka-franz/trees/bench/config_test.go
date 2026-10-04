package main

import (
	"errors"
	"flag"
	"testing"
	"time"
)

func TestParseConfigDefaults(t *testing.T) {
	t.Setenv("KAFKA_BROKERS", "")
	c, err := parseConfig(nil)
	if err != nil {
		t.Fatal(err)
	}
	if c.Topic != "bench" || c.Partitions != 12 || c.Replication != 3 || c.Rate != 10000 || c.Size != 1024 {
		t.Errorf("unexpected defaults: %+v", c)
	}
	if v := c.TopicConfigs["min.insync.replicas"]; v == nil || *v != "2" {
		t.Errorf("unexpected topic configs: %v", c.TopicConfigs)
	}
}

func TestParseConfigEnvBrokers(t *testing.T) {
	t.Setenv("KAFKA_BROKERS", "redpanda-0:9092,redpanda-1:9092")
	c, err := parseConfig(nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(c.Brokers) != 2 || c.Brokers[0] != "redpanda-0:9092" {
		t.Errorf("unexpected brokers: %v", c.Brokers)
	}
}

func TestParseConfigFlags(t *testing.T) {
	c, err := parseConfig([]string{"-brokers", "localhost:29092", "-rate", "0", "-duration", "10s", "-size", "100", "-txns", "5", "-topic.configs", ""})
	if err != nil {
		t.Fatal(err)
	}
	if c.Rate != 0 || c.Duration != 10*time.Second || c.Size != 100 || c.Txns != 5 || c.TopicConfigs != nil {
		t.Errorf("unexpected config: %+v", c)
	}
}

func TestParseConfigInvalid(t *testing.T) {
	for _, args := range [][]string{
		{"-brokers", ","},
		{"-topic", ""},
		{"-rate", "-1"},
		{"-size", "8"},
		{"-duration", "1s", "-warmup", "2s"},
		{"-txns", "-1"},
		{"-topic.configs", "nokey"},
		{"-compression", "brotli"},
	} {
		if _, err := parseConfig(args); err == nil {
			t.Errorf("parseConfig(%v) expected an error", args)
		}
	}
	if _, err := parseConfig([]string{"-h"}); !errors.Is(err, flag.ErrHelp) {
		t.Errorf("parseConfig(-h) = %v, want flag.ErrHelp", err)
	}
}
