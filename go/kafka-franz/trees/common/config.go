// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"fmt"
	"strings"

	"github.com/twmb/franz-go/pkg/kgo"
)

// ParsePartitioner returns the franz-go partitioner for a partitioner name
func ParsePartitioner(name string) (kgo.Partitioner, error) {
	switch name {
	case PartitionHash:
		return kgo.StickyKeyPartitioner(nil), nil // murmur2 hashing of the key, same as the java client (nil hasher). Keyless records are sticky
	case PartitionRand:
		return kgo.StickyPartitioner(), nil // a random partition is chosen per batch (keys are ignored)
	case PartitionRoundRobin:
		return kgo.RoundRobinPartitioner(), nil
	default:
		return nil, fmt.Errorf("unknown kafka partitioner %q, supported: %v", name, SupportedPartitioners)
	}
}

// ParseBalancer returns the franz-go consumer group balancer for a balancer name
func ParseBalancer(name string) (kgo.GroupBalancer, error) {
	switch name {
	case BalancerRange:
		return kgo.RangeBalancer(), nil
	case BalancerRoundRobin:
		return kgo.RoundRobinBalancer(), nil
	case BalancerSticky:
		return kgo.StickyBalancer(), nil
	case BalancerCooperative:
		return kgo.CooperativeStickyBalancer(), nil
	default:
		return nil, fmt.Errorf("unknown consumer group balancer %q, supported: %v", name, SupportedBalancers)
	}
}

// ResetOffset returns where a consumer group starts when it has no committed offset
func ResetOffset(oldest bool) kgo.Offset {
	if oldest {
		return kgo.NewOffset().AtStart()
	}
	return kgo.NewOffset().AtEnd()
}

// ParseTopicConfigs converts a comma separated list of `key=value` pairs (e.g. `retention.ms=60000,cleanup.policy=delete`)
// into the topic configs used by the kadm create topics request
func ParseTopicConfigs(s string) (map[string]*string, error) {
	pairs := SplitList(s)
	if len(pairs) == 0 {
		return nil, nil
	}
	configs := make(map[string]*string, len(pairs))
	for _, pair := range pairs {
		key, value, ok := strings.Cut(pair, "=")
		key, value = strings.TrimSpace(key), strings.TrimSpace(value)
		if !ok || key == "" || value == "" {
			return nil, fmt.Errorf("invalid topic config %q, expected key=value", pair)
		}
		if _, dup := configs[key]; dup {
			return nil, fmt.Errorf("duplicate topic config %q", key)
		}
		configs[key] = &value
	}
	return configs, nil
}

// ValidateBrokers makes sure that at least one broker is provided
func ValidateBrokers(brokers []string) error {
	if len(brokers) == 0 {
		return fmt.Errorf("at least one kafka broker is required")
	}
	return nil
}
