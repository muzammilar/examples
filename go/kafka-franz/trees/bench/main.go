// The module trees/bench is a benchmark for any Kafka-API cluster (Apache Kafka, Redpanda, ...).
//
// It re-creates a topic, produces at a fixed rate (or as fast as it can) with acks=all and the
// idempotent producer, consumes the topic live to measure end-to-end latency (send time to
// consume time, both on this host), then reads the topic back from offset 0 and checks that
// every acknowledged record is there exactly once (count + CRC32 checksum). Optionally it runs
// transactions and checks read_committed. Prints a readable report and a one-line JSON summary;
// exits 1 if a check fails.
//
// Record value: run id (8 B) | sequence (8 B) | random payload up to -size. The send time is
// the record timestamp (ms) and, with ns resolution, the `send-ns` header.

package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"math/rand/v2"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/muzammilar/examples-go/kafka-franz/trees/common"
	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"
)

func main() {
	conf, err := parseConfig(os.Args[1:])
	if errors.Is(err, flag.ErrHelp) {
		return
	} else if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	b := &bench{c: conf, runID: rand.Uint64(), payload: newPayload(conf.Size - minSize)}
	b.hooks = append(b.hooks, kgo.WithLogger(common.KgoLogger(conf.Verbose)))
	if conf.MetricsAddr != "" {
		b.hooks = append(b.hooks, common.StartMetricsServer("treebench", conf.MetricsAddr, common.InitLogger("info")))
	}
	admin := b.client()
	defer admin.Close()
	b.adm = kadm.NewClient(admin)
	if err := waitForBrokers(ctx, b.adm, int(conf.Replication)); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}

	mode := fmt.Sprintf("%d records/s", conf.Rate)
	if conf.Rate == 0 {
		mode = "max rate"
	}
	fmt.Printf("bench %s: %s, %d B records, %s (warm-up %s), topic %s (%d partitions, RF %d, %v), acks=all, idempotent, linger %s, compression %s; run %x\n",
		conf.Label, mode, conf.Size, conf.Duration, conf.Warmup, conf.Topic, conf.Partitions, conf.Replication,
		topicConfigString(conf.TopicConfigs), conf.Linger, conf.Codec, b.runID)
	res, err := b.run(ctx)
	if err != nil {
		fmt.Fprintln(os.Stderr, "bench:", err)
		os.Exit(2)
	}
	res.Brokers = strings.Join(conf.Brokers, ",")
	fmt.Printf("  produced   %d acked in %.1f s = %.0f records/s = %.1f MiB/s, %d failed; consumed live %d\n",
		res.Acked, res.DurationS, res.AckedPerS, res.MiBPerS, res.Failed, res.ConsumedLive)
	fmt.Printf("  ack        %s\n", res.Ack)
	fmt.Printf("  end-to-end %s\n", res.E2E)
	fmt.Printf("  read back  %d records: missing %d, duplicates %d, checksum ok %v\n", res.Read, res.Missing, res.Duplicates, res.ChecksumOK)

	if conf.Txns > 0 {
		tr, err := b.txns(ctx)
		if err != nil {
			fmt.Fprintln(os.Stderr, "transactions:", err)
			os.Exit(2)
		}
		res.Txn = tr
		res.OK = res.OK && tr.OK
		fmt.Printf("  txns       %d x %d records, every 5th aborted: committed %d, aborted %d; begin..commit %s\n",
			conf.Txns, conf.TxnRecords, tr.Committed, tr.Aborted, tr.Commit)
		fmt.Printf("             read_committed %d (checksum ok %v), read_uncommitted %d (aborted records are in the log, hidden from read_committed)\n",
			tr.ReadCommitted, tr.ChecksumOK, tr.ReadUncommitted)
	}
	if !conf.Keep {
		_, _ = b.adm.DeleteTopics(context.Background(), conf.Topic, conf.Topic+"-txn")
	}
	line, _ := json.Marshal(res)
	fmt.Printf("RESULT %s\n", line)
	if !res.OK {
		fmt.Println("CHECK FAILED")
		os.Exit(1)
	}
	fmt.Println("CHECK PASSED")
}

// waitForBrokers waits (up to 2 minutes) until at least n brokers are in the metadata
func waitForBrokers(ctx context.Context, adm *kadm.Client, n int) error {
	deadline := time.Now().Add(2 * time.Minute)
	for {
		m, err := adm.BrokerMetadata(ctx)
		if err == nil && len(m.Brokers) >= n {
			return nil
		}
		if time.Now().After(deadline) || ctx.Err() != nil {
			return fmt.Errorf("fewer than %d brokers reachable: %v", n, err)
		}
		time.Sleep(time.Second)
	}
}

func topicConfigString(m map[string]*string) string {
	var s []string
	for k, v := range m {
		s = append(s, k+"="+*v)
	}
	return strings.Join(s, ",")
}
