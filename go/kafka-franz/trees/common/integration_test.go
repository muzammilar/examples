//go:build integration

// Integration tests and benchmarks against a running kafka cluster (see `make up` and `make test-integration`).
// The brokers default to the ones exposed on the host by the docker compose setup and can be overridden with KAFKA_BROKERS.

package common

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"
)

const hostBrokers = "localhost:29092,localhost:39092,localhost:49092"

var discard = slog.New(slog.NewTextHandler(io.Discard, nil))

func integrationBrokers() []string {
	if b := os.Getenv("KAFKA_BROKERS"); b != "" {
		return SplitList(b)
	}
	return SplitList(hostBrokers)
}

// newIntegrationClient creates a client and fails fast if kafka is not reachable
func newIntegrationClient(tb testing.TB, opts ...kgo.Opt) *kgo.Client {
	tb.Helper()
	client, err := NewClient(integrationBrokers(), "treetest", false, opts...)
	if err != nil {
		tb.Fatal(err)
	}
	tb.Cleanup(client.Close)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := client.Ping(ctx); err != nil {
		tb.Fatalf("kafka is not reachable at %v (run `make up` or set KAFKA_BROKERS): %v", integrationBrokers(), err)
	}
	return client
}

// createTestTopic creates a uniquely named topic that is deleted when the test finishes
func createTestTopic(tb testing.TB, client *kgo.Client, partitions int32) string {
	tb.Helper()
	topic := fmt.Sprintf("franz-test-%d", time.Now().UnixNano())
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	if err := CreateTopicsIfNotExist(ctx, client, partitions, -1, nil, discard, topic); err != nil {
		tb.Fatal(err)
	}
	tb.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		_, _ = kadm.NewClient(client).DeleteTopics(ctx, topic)
	})
	if err := WaitForTopics(ctx, client, discard, topic); err != nil {
		tb.Fatal(err)
	}
	return topic
}

func TestIntegrationCreateTopicsIfNotExist(t *testing.T) {
	client := newIntegrationClient(t)
	retention := "3600000"
	topic := fmt.Sprintf("franz-test-create-%d", time.Now().UnixNano())
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	adm := kadm.NewClient(client)
	t.Cleanup(func() { _, _ = adm.DeleteTopics(context.Background(), topic) })

	configs := map[string]*string{"retention.ms": &retention}
	if err := CreateTopicsIfNotExist(ctx, client, 5, -1, configs, discard, topic); err != nil {
		t.Fatal(err)
	}
	// creating an existing topic is a no-op
	if err := CreateTopicsIfNotExist(ctx, client, 5, -1, configs, discard, topic); err != nil {
		t.Fatalf("second create: %v", err)
	}
	if err := WaitForTopics(ctx, client, discard, topic); err != nil {
		t.Fatal(err)
	}

	details, err := adm.ListTopics(ctx, topic)
	if err != nil {
		t.Fatal(err)
	}
	if got := len(details[topic].Partitions); got != 5 {
		t.Errorf("topic has %d partitions, want 5", got)
	}
	rcs, err := adm.DescribeTopicConfigs(ctx, topic)
	if err != nil {
		t.Fatal(err)
	}
	rc, err := rcs.On(topic, nil)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, c := range rc.Configs {
		if c.Key == "retention.ms" {
			found = true
			if c.MaybeValue() != retention {
				t.Errorf("retention.ms = %q, want %q", c.MaybeValue(), retention)
			}
		}
	}
	if !found {
		t.Error("retention.ms config not found")
	}
}

func TestIntegrationProduceConsumeGroup(t *testing.T) {
	const numRecords = 200
	producer := newIntegrationClient(t, kgo.RecordPartitioner(kgo.StickyKeyPartitioner(nil)))
	topic := createTestTopic(t, producer, 3)
	ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
	defer cancel()

	// produce
	want := make(map[int]Message, numRecords)
	records := make([]*kgo.Record, 0, numRecords)
	for i := 0; i < numRecords; i++ {
		r, err := NewRecord(topic, UserIDMin+i, i%2)
		if err != nil {
			t.Fatal(err)
		}
		records = append(records, r)
		want[UserIDMin+i] = NewMessage(UserIDMin+i, i%2)
	}
	if err := producer.ProduceSync(ctx, records...).FirstErr(); err != nil {
		t.Fatal(err)
	}

	// consume with a consumer group, marking records and committing like the consumer binary
	balancer, err := ParseBalancer(DefaultBalancer)
	if err != nil {
		t.Fatal(err)
	}
	group := topic + "-group"
	consumer := newIntegrationClient(t,
		kgo.ConsumerGroup(group),
		kgo.ConsumeTopics(topic),
		kgo.Balancers(balancer),
		kgo.ConsumeResetOffset(ResetOffset(true)),
		kgo.AutoCommitMarks(),
	)
	t.Cleanup(func() { _, _ = kadm.NewClient(producer).DeleteGroup(context.Background(), group) })

	got := make(map[int]Message, numRecords)
	partitionOf := make(map[string]int32) // the same key must always land on the same partition
	for len(got) < numRecords {
		fetches := consumer.PollFetches(ctx)
		if ctx.Err() != nil {
			t.Fatalf("timed out after consuming %d/%d records", len(got), numRecords)
		}
		fetches.EachError(func(_ string, p int32, err error) { t.Errorf("fetch error on partition %d: %v", p, err) })
		fetches.EachRecord(func(r *kgo.Record) {
			m, err := DecodeMessage(r.Value)
			if err != nil {
				t.Errorf("decode offset %d: %v", r.Offset, err)
				return
			}
			if p, ok := partitionOf[string(r.Key)]; ok && p != r.Partition {
				t.Errorf("key %s on partitions %d and %d", r.Key, p, r.Partition)
			}
			partitionOf[string(r.Key)] = r.Partition
			got[m.UserId] = m
			consumer.MarkCommitRecords(r)
		})
	}
	for id, m := range want {
		if got[id] != m {
			t.Errorf("user %d: got %+v, want %+v", id, got[id], m)
		}
	}

	// the committed offsets of the group cover all the records
	if err := consumer.CommitMarkedOffsets(ctx); err != nil {
		t.Fatal(err)
	}
	offsets, err := kadm.NewClient(producer).FetchOffsets(ctx, group)
	if err != nil {
		t.Fatal(err)
	}
	var committed int64
	offsets.Each(func(o kadm.OffsetResponse) { committed += o.At })
	if committed != numRecords {
		t.Errorf("committed offsets sum to %d, want %d", committed, numRecords)
	}
}

/*
 * Benchmarks
 */

// BenchmarkIntegrationProduceSync measures the round trip of producing one record at a time
func BenchmarkIntegrationProduceSync(b *testing.B) {
	client := newIntegrationClient(b)
	topic := createTestTopic(b, client, 3)
	ctx := context.Background()
	r, _ := NewRecord(topic, UserIDMin, ProducerSync)

	b.ReportAllocs()
	b.SetBytes(int64(len(r.Value)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rec, _ := NewRecord(topic, UserIDMin+i%UserIDRange, ProducerSync)
		if err := client.ProduceSync(ctx, rec).FirstErr(); err != nil {
			b.Fatal(err)
		}
	}
}

// BenchmarkIntegrationProduceAsync measures the batched (buffered) produce throughput
func BenchmarkIntegrationProduceAsync(b *testing.B) {
	client := newIntegrationClient(b, kgo.ProducerLinger(5*time.Millisecond))
	topic := createTestTopic(b, client, 3)
	ctx := context.Background()
	r, _ := NewRecord(topic, UserIDMin, ProducerAsync)

	var (
		mu       sync.Mutex
		firstErr error
	)
	promise := func(_ *kgo.Record, err error) {
		if err != nil {
			mu.Lock()
			if firstErr == nil {
				firstErr = err
			}
			mu.Unlock()
		}
	}

	b.ReportAllocs()
	b.SetBytes(int64(len(r.Value)))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		rec, _ := NewRecord(topic, UserIDMin+i%UserIDRange, ProducerAsync)
		client.Produce(ctx, rec, promise)
	}
	if err := client.Flush(ctx); err != nil {
		b.Fatal(err)
	}
	b.StopTimer()
	if firstErr != nil {
		b.Fatal(firstErr)
	}
}
