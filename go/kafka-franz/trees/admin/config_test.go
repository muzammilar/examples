package main

import (
	"errors"
	"flag"
	"reflect"
	"testing"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
)

func TestParseConfigDefaults(t *testing.T) {
	c, err := parseConfig(nil)
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(c.Brokers, common.SplitList(common.DefaultKafkaBrokers)) || !reflect.DeepEqual(c.Topics, []string{common.DefaultTopic}) {
		t.Errorf("unexpected brokers/topics: %v %v", c.Brokers, c.Topics)
	}
	if c.Partitions != common.DefaultPartitions || c.ReplicationFactor != common.DefaultReplicationFactor || c.TopicConfigs != nil {
		t.Errorf("unexpected defaults: %+v", c)
	}
}

func TestParseConfigFlags(t *testing.T) {
	c, err := parseConfig([]string{"-brokers", "a:1,b:2", "-topics", "trees,test", "-partitions", "-1", "-replication", "1", "-configs", "retention.ms=1000"})
	if err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(c.Topics, []string{"trees", "test"}) || c.Partitions != -1 || c.ReplicationFactor != 1 {
		t.Errorf("unexpected config: %+v", c)
	}
	if v := c.TopicConfigs["retention.ms"]; v == nil || *v != "1000" {
		t.Errorf("unexpected topic configs: %v", c.TopicConfigs)
	}
}

func TestParseConfigInvalid(t *testing.T) {
	for _, args := range [][]string{
		{"-brokers", ""},
		{"-topics", " , "},
		{"-partitions", "0"},
		{"-partitions", "-2"},
		{"-replication", "0"},
		{"-replication", "40000"},
		{"-configs", "retention.ms"},
		{"-unknown"},
	} {
		if _, err := parseConfig(args); err == nil {
			t.Errorf("parseConfig(%v) expected an error", args)
		}
	}
	if _, err := parseConfig([]string{"-help"}); !errors.Is(err, flag.ErrHelp) {
		t.Errorf("parseConfig(-help) = %v, want flag.ErrHelp", err)
	}
}
