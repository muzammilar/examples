package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	demov1 "example.com/kafka-enricher/kafka/demo/v1"
	"github.com/twmb/franz-go/pkg/kgo"
	"google.golang.org/protobuf/proto"
)

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

type counters struct {
	committed atomic.Uint64
	bytes     atomic.Uint64
	aborted   atomic.Uint64
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		client := http.Client{Timeout: 2 * time.Second}
		response, err := client.Get("http://127.0.0.1:8080/ready")
		if err != nil {
			os.Exit(1)
		}
		defer response.Body.Close()
		if response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		return
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if err := run(ctx); err != nil && !(ctx.Err() != nil && errors.Is(err, context.Canceled)) {
		log.Fatal(err)
	}
}

func run(parent context.Context) error {
	ctx, cancel := context.WithCancelCause(parent)
	defer cancel(nil)
	bootstrap := strings.Split(env("BOOTSTRAP_SERVERS", "kafka-broker-1:19092"), ",")
	expected, err := strconv.Atoi(env("EXPECTED_CUSTOMERS", "10000"))
	if err != nil || expected < 1 {
		return errors.New("EXPECTED_CUSTOMERS must be positive")
	}
	state := newCustomerState()
	ready := make(chan struct{})
	metrics := new(counters)
	isReady := func() bool {
		select {
		case <-ready:
			return ctx.Err() == nil
		default:
			return false
		}
	}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /ready", func(w http.ResponseWriter, _ *http.Request) {
		if !isReady() {
			http.Error(w, "customer cache not ready", http.StatusServiceUnavailable)
			return
		}
		_, _ = w.Write([]byte("ready\n"))
	})
	mux.HandleFunc("GET /status", func(w http.ResponseWriter, _ *http.Request) {
		count, applied, probe, positions := state.snapshot()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(struct {
			Ready     bool            `json:"ready"`
			Customers int             `json:"customers"`
			Applied   uint64          `json:"cdc_applied"`
			Probe     int64           `json:"cdc_probe"`
			Positions map[int32]int64 `json:"cdc_positions"`
			Committed uint64          `json:"committed_orders"`
			Bytes     uint64          `json:"committed_bytes"`
			Aborted   uint64          `json:"aborted_transactions"`
		}{isReady(), count, applied, probe, positions, metrics.committed.Load(), metrics.bytes.Load(), metrics.aborted.Load()})
	})
	server := &http.Server{Addr: ":8080", Handler: mux, ReadHeaderTimeout: 5 * time.Second, WriteTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second}
	go func() {
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			cancel(err)
		}
	}()
	defer func() {
		shutdown, done := context.WithTimeout(context.Background(), 3*time.Second)
		defer done()
		_ = server.Shutdown(shutdown)
	}()
	cache, err := kgo.NewClient(kgo.SeedBrokers(bootstrap...), kgo.ConsumeTopics(env("CUSTOMER_TOPIC", "pg.public.customers")),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()), kgo.FetchIsolationLevel(kgo.ReadCommitted()), kgo.FetchMaxBytes(8<<20))
	if err != nil {
		return fmt.Errorf("create CDC consumer: %w", err)
	}
	cacheDone := make(chan struct{})
	go func() {
		defer close(cacheDone)
		if err := consumeCustomers(ctx, func(ctx context.Context) kgo.Fetches { return cache.PollRecords(ctx, 5000) }, state, expected, ready); err != nil && ctx.Err() == nil {
			cancel(err)
		}
	}()
	defer func() { cancel(nil); cache.Close(); <-cacheDone }()
	select {
	case <-ready:
	case <-ctx.Done():
		return context.Cause(ctx)
	}
	log.Printf("loaded %d customers; CDC consumption remains active", expected)
	input, output := env("INPUT_TOPIC", "orders"), env("OUTPUT_TOPIC", "orders.enriched")
	session, err := kgo.NewGroupTransactSession(kgo.SeedBrokers(bootstrap...),
		kgo.TransactionalID(env("TRANSACTIONAL_ID", "orders-enricher-v1")), kgo.ConsumerGroup(env("CONSUMER_GROUP", "orders-enricher")),
		kgo.ConsumeTopics(input), kgo.FetchIsolationLevel(kgo.ReadCommitted()), kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()),
		kgo.DisableAutoCommit(), kgo.FetchMaxBytes(8<<20), kgo.RecordPartitioner(kgo.ManualPartitioner()),
		kgo.ProducerBatchCompression(kgo.Lz4Compression()), kgo.ProducerLinger(5*time.Millisecond))
	if err != nil {
		return fmt.Errorf("create transaction session: %w", err)
	}
	defer session.CloseAllowingRebalance()
	for {
		if ctx.Err() != nil {
			return context.Cause(ctx)
		}
		if err := session.Begin(); err != nil {
			return fmt.Errorf("begin transaction: %w", err)
		}
		fetches := session.PollRecords(ctx, 5000)
		if ctx.Err() != nil {
			return context.Cause(ctx)
		}
		if errs := fetches.Errors(); len(errs) > 0 {
			_, abortErr := session.End(ctx, kgo.TryAbort)
			return fmt.Errorf("order fetch: %v (abort: %v)", errs[0].Err, abortErr)
		}
		var records []*kgo.Record
		var processErr error
		var bytes uint64
		fetches.EachRecord(func(record *kgo.Record) {
			if processErr != nil {
				return
			}
			var order demov1.Order
			if err := proto.Unmarshal(record.Value, &order); err != nil {
				processErr = fmt.Errorf("decode order: %w", err)
				return
			}
			if order.OrderId == "" || string(record.Key) != order.OrderId {
				processErr = errors.New("order key/body mismatch")
				return
			}
			customer, ok := state.get(order.CustomerId)
			if !ok {
				processErr = fmt.Errorf("customer %q missing from CDC cache", order.CustomerId)
				return
			}
			value, err := proto.Marshal(&demov1.EnrichedOrder{Order: &order, Customer: customer})
			if err != nil {
				processErr = err
				return
			}
			bytes += uint64(len(value))
			// Matching partition counts preserve source partition ordering end to end.
			records = append(records, &kgo.Record{Topic: output, Partition: record.Partition, Key: record.Key, Value: value})
		})
		if processErr == nil && len(records) > 0 {
			processErr = session.ProduceSync(ctx, records...).FirstErr()
		}
		if processErr != nil {
			_, abortErr := session.End(ctx, kgo.TryAbort)
			return fmt.Errorf("process transaction: %v (abort: %v)", processErr, abortErr)
		}
		committed, err := session.End(ctx, kgo.TryCommit)
		if err != nil {
			return fmt.Errorf("end transaction: %w", err)
		}
		if committed {
			metrics.committed.Add(uint64(len(records)))
			metrics.bytes.Add(bytes)
		} else {
			metrics.aborted.Add(1)
		}
	}
}
