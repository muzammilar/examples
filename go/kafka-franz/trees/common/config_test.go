package common

import (
	"testing"
)

func TestParsePartitioner(t *testing.T) {
	for _, name := range SupportedPartitioners {
		if p, err := ParsePartitioner(name); err != nil || p == nil {
			t.Errorf("ParsePartitioner(%q) = %v, %v", name, p, err)
		}
	}
	if _, err := ParsePartitioner("murmur"); err == nil {
		t.Error("expected an error for an unknown partitioner")
	}
}

func TestParseBalancer(t *testing.T) {
	for _, name := range SupportedBalancers {
		b, err := ParseBalancer(name)
		if err != nil {
			t.Fatalf("ParseBalancer(%q) error: %v", name, err)
		}
		// the protocol name advertised to kafka matches the flag value
		if b.ProtocolName() != name {
			t.Errorf("ParseBalancer(%q).ProtocolName() = %q", name, b.ProtocolName())
		}
	}
	if _, err := ParseBalancer("random"); err == nil {
		t.Error("expected an error for an unknown balancer")
	}
}

func TestResetOffset(t *testing.T) {
	if got := ResetOffset(true).EpochOffset().Offset; got != -2 { // -2 is `earliest` in the kafka protocol
		t.Errorf("ResetOffset(true) offset = %d, want -2", got)
	}
	if got := ResetOffset(false).EpochOffset().Offset; got != -1 { // -1 is `latest` in the kafka protocol
		t.Errorf("ResetOffset(false) offset = %d, want -1", got)
	}
}

func TestParseTopicConfigs(t *testing.T) {
	got, err := ParseTopicConfigs(" retention.ms=60000, cleanup.policy = delete ")
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]string{"retention.ms": "60000", "cleanup.policy": "delete"}
	if len(got) != len(want) {
		t.Fatalf("got %d configs, want %d", len(got), len(want))
	}
	for k, v := range want {
		if got[k] == nil || *got[k] != v {
			t.Errorf("config %q = %v, want %q", k, got[k], v)
		}
	}

	if got, err := ParseTopicConfigs(""); err != nil || got != nil {
		t.Errorf("ParseTopicConfigs(\"\") = %v, %v, want nil, nil", got, err)
	}
	for _, bad := range []string{"retention.ms", "=1", "a=", "a=1,a=2"} {
		if _, err := ParseTopicConfigs(bad); err == nil {
			t.Errorf("ParseTopicConfigs(%q) expected an error", bad)
		}
	}
}

func TestValidateBrokers(t *testing.T) {
	if err := ValidateBrokers(nil); err == nil {
		t.Error("expected an error for no brokers")
	}
	if err := ValidateBrokers([]string{"localhost:9092"}); err != nil {
		t.Error(err)
	}
}
