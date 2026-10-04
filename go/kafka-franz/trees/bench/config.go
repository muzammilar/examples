// The module trees/bench is a benchmark for any Kafka-API cluster (Apache Kafka, Redpanda, ...)

package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kgo"
)

// config is the benchmark configuration
type config struct {
	Brokers      []string
	Label        string
	Topic        string
	Partitions   int32
	Replication  int16
	TopicConfigs map[string]*string
	Rate         int // records/s; 0 = as fast as possible
	Duration     time.Duration
	Warmup       time.Duration
	Size         int
	Linger       time.Duration
	Compression  kgo.CompressionCodec
	Codec        string
	Txns         int
	TxnRecords   int
	MetricsAddr  string
	Keep         bool
	Verbose      bool
}

// minimum record value: run id (8 B) | sequence (8 B)
const minSize = 16

// parseConfig parses and validates the command line flags. The brokers default to $KAFKA_BROKERS.
func parseConfig(args []string) (*config, error) {
	fs := flag.NewFlagSet("treebench", flag.ContinueOnError)
	defBrokers := common.DefaultKafkaBrokers
	if b := os.Getenv("KAFKA_BROKERS"); b != "" {
		defBrokers = b
	}
	brokers := fs.String("brokers", defBrokers, "Kafka bootstrap brokers, comma separated (default $KAFKA_BROKERS or the compose brokers)")
	label := fs.String("label", "", "Label for the JSON summary (e.g. the system and version)")
	topic := fs.String("topic", "bench", "Topic to benchmark; deleted and re-created at the start")
	partitions := fs.Int("partitions", 12, "Partitions of the benchmark topic")
	replication := fs.Int("replication", 3, "Replication factor of the benchmark topic")
	topicConfigs := fs.String("topic.configs", "min.insync.replicas=2", "Topic configs as key=value,key=value")
	rate := fs.Int("rate", 10000, "Target records per second; 0 produces as fast as the client can")
	duration := fs.Duration("duration", 30*time.Second, "How long to produce")
	warmup := fs.Duration("warmup", 2*time.Second, "Records sent in the first part of the run are verified but not timed")
	size := fs.Int("size", 1024, "Record value size in bytes (at least 16)")
	linger := fs.Duration("linger", 0, "Producer linger (0: send as soon as possible)")
	codec := fs.String("compression", "none", "Producer compression: none, snappy, lz4, zstd, gzip (the payload is one random string repeated, so it compresses well)")
	txns := fs.Int("txns", 0, "After the run, this many transactions (every 5th aborted) on <topic>-txn; 0 skips")
	txnRecords := fs.Int("txn.records", 1000, "Records per transaction")
	keep := fs.Bool("keep", false, "Keep the benchmark topics afterwards (default: delete them to free the disk)")
	metricsAddr := fs.String("metrics.addr", "", "Address to expose prometheus metrics on (empty: off)")
	verbose := fs.Bool("log.kgo", false, "Enable franz-go debug logging to stderr")
	if err := fs.Parse(args); err != nil {
		return nil, err
	}

	c := &config{
		Brokers:     common.SplitList(*brokers),
		Label:       *label,
		Topic:       *topic,
		Partitions:  int32(*partitions),
		Replication: int16(*replication),
		Rate:        *rate,
		Duration:    *duration,
		Warmup:      *warmup,
		Size:        *size,
		Linger:      *linger,
		Codec:       *codec,
		Txns:        *txns,
		TxnRecords:  *txnRecords,
		MetricsAddr: *metricsAddr,
		Keep:        *keep,
		Verbose:     *verbose,
	}
	var err error
	if c.Compression, err = parseCodec(c.Codec); err != nil {
		return nil, err
	}
	if c.TopicConfigs, err = common.ParseTopicConfigs(*topicConfigs); err != nil {
		return nil, err
	}
	switch {
	case common.ValidateBrokers(c.Brokers) != nil:
		return nil, common.ValidateBrokers(c.Brokers)
	case c.Topic == "":
		return nil, fmt.Errorf("a topic is required")
	case c.Partitions < 1 || c.Replication < 1:
		return nil, fmt.Errorf("partitions and replication must be at least 1")
	case c.Rate < 0:
		return nil, fmt.Errorf("the rate must be 0 (max) or positive, got %d", c.Rate)
	case c.Duration <= 0 || c.Warmup < 0 || c.Warmup >= c.Duration:
		return nil, fmt.Errorf("need duration > warmup >= 0, got %s and %s", c.Duration, c.Warmup)
	case c.Size < minSize:
		return nil, fmt.Errorf("the record size must be at least %d bytes, got %d", minSize, c.Size)
	case c.Txns < 0 || c.TxnRecords < 1:
		return nil, fmt.Errorf("txns must be >= 0 and txn.records >= 1")
	}
	return c, nil
}

func parseCodec(name string) (kgo.CompressionCodec, error) {
	switch name {
	case "none":
		return kgo.NoCompression(), nil
	case "snappy":
		return kgo.SnappyCompression(), nil
	case "lz4":
		return kgo.Lz4Compression(), nil
	case "zstd":
		return kgo.ZstdCompression(), nil
	case "gzip":
		return kgo.GzipCompression(), nil
	}
	return kgo.CompressionCodec{}, fmt.Errorf("unknown compression %q (none, snappy, lz4, zstd, gzip)", name)
}
