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
	if c.Group != common.DefaultConsumerGroup || !reflect.DeepEqual(c.Topics, []string{common.DefaultTopic}) {
		t.Errorf("unexpected defaults: %+v", c)
	}
	if c.Balancer.ProtocolName() != common.DefaultBalancer {
		t.Errorf("unexpected balancer %q", c.Balancer.ProtocolName())
	}
	if c.ResetOffset.EpochOffset().Offset != -2 { // oldest by default
		t.Errorf("unexpected reset offset %v", c.ResetOffset)
	}
}

func TestParseConfigFlags(t *testing.T) {
	c, err := parseConfig([]string{"-group", "g", "-topics", "test,trees", "-balancer", common.BalancerRange, "-oldest=false"})
	if err != nil {
		t.Fatal(err)
	}
	if c.Group != "g" || !reflect.DeepEqual(c.Topics, []string{"test", "trees"}) || c.Balancer.ProtocolName() != common.BalancerRange {
		t.Errorf("unexpected config: %+v", c)
	}
	if c.ResetOffset.EpochOffset().Offset != -1 { // newest
		t.Errorf("unexpected reset offset %v", c.ResetOffset)
	}
}

func TestParseConfigInvalid(t *testing.T) {
	for _, args := range [][]string{
		{"-brokers", ""},
		{"-group", ""},
		{"-topics", ","},
		{"-balancer", "random"},
	} {
		if _, err := parseConfig(args); err == nil {
			t.Errorf("parseConfig(%v) expected an error", args)
		}
	}
	if _, err := parseConfig([]string{"-help"}); !errors.Is(err, flag.ErrHelp) {
		t.Errorf("parseConfig(-help) = %v, want flag.ErrHelp", err)
	}
}
