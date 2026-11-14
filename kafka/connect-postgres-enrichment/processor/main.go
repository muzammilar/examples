package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"example.com/kafka-enricher/kafka/demo/v1"
	"github.com/twmb/franz-go/pkg/kgo"
	"google.golang.org/protobuf/proto"
)

type customerState struct {
	mu   sync.RWMutex
	byID map[string]*demov1.Customer
}

type cdcRecord struct {
	After *struct {
		ID          string `json:"id"`
		DisplayName string `json:"display_name"`
		Segment     string `json:"segment"`
	} `json:"after"`
	Before *struct {
		ID string `json:"id"`
	} `json:"before"`
	Op string `json:"op"`
}

func unwrapConnectPayload(raw json.RawMessage) json.RawMessage {
	var message struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &message); err == nil && message.Payload != nil {
		return message.Payload
	}
	return raw
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func (s *customerState) apply(record *kgo.Record) error {
	if record.Key == nil {
		return errors.New("CDC record has no key")
	}
	var key struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(unwrapConnectPayload(record.Key), &key); err != nil {
		return fmt.Errorf("decode customer key: %w", err)
	}
	if key.ID == "" {
		return errors.New("CDC key has empty customer id")
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if record.Value == nil {
		delete(s.byID, key.ID)
		return nil
	}
	var change cdcRecord
	if err := json.Unmarshal(unwrapConnectPayload(record.Value), &change); err != nil {
		return fmt.Errorf("decode Debezium envelope: %w", err)
	}
	if change.Op == "d" || change.After == nil {
		delete(s.byID, key.ID)
		return nil
	}
	after := change.After
	s.byID[after.ID] = &demov1.Customer{CustomerId: after.ID, DisplayName: after.DisplayName, Segment: after.Segment}
	return nil
}

func (s *customerState) get(id string) (*demov1.Customer, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	value, ok := s.byID[id]
	if !ok {
		return nil, false
	}
	copy := *value
	return &copy, true
}

func main() {
	if len(os.Args) == 2 && os.Args[1] == "healthcheck" {
		response, err := http.Get("http://127.0.0.1:8080/ready")
		if err != nil || response.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		_ = response.Body.Close()
		return
	}
	if err := run(); err != nil {
		log.Fatal(err)
	}
}

func run() error {
	bootstrap := strings.Split(env("BOOTSTRAP_SERVERS", "kafka-broker-1:19092"), ",")
	inputTopic := env("INPUT_TOPIC", "orders")
	outputTopic := env("OUTPUT_TOPIC", "orders.enriched")
	customerTopic := env("CUSTOMER_TOPIC", "pg.public.customers")
	expected, err := strconv.Atoi(env("EXPECTED_CUSTOMERS", "10"))
	if err != nil || expected < 1 {
		return errors.New("EXPECTED_CUSTOMERS must be positive")
	}
	state := &customerState{byID: make(map[string]*demov1.Customer)}
	ready := make(chan struct{})
	http.HandleFunc("/ready", func(w http.ResponseWriter, _ *http.Request) {
		select {
		case <-ready:
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte("ready\n"))
		default:
			http.Error(w, "customer CDC snapshot not loaded", http.StatusServiceUnavailable)
		}
	})
	go func() { log.Printf("health endpoint: %v", http.ListenAndServe(":8080", nil)) }()

	cacheGroup := fmt.Sprintf("customer-cache-%d", time.Now().UnixNano())
	cache, err := kgo.NewClient(kgo.SeedBrokers(bootstrap...), kgo.ConsumerGroup(cacheGroup),
		kgo.ConsumeTopics(customerTopic), kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()))
	if err != nil {
		return fmt.Errorf("create customer CDC consumer: %w", err)
	}
	defer cache.Close()
	ctx := context.Background()
	log.Printf("loading %d customer rows from compacted topic %s", expected, customerTopic)
	for {
		fetches := cache.PollFetches(ctx)
		if errs := fetches.Errors(); len(errs) > 0 {
			return fmt.Errorf("customer CDC fetch: %v", errs[0].Err)
		}
		var applyErr error
		fetches.EachRecord(func(record *kgo.Record) {
			if applyErr == nil {
				applyErr = state.apply(record)
			}
		})
		if applyErr != nil {
			return applyErr
		}
		state.mu.RLock()
		loaded := len(state.byID)
		state.mu.RUnlock()
		if loaded >= expected {
			break
		}
	}
	close(ready)
	log.Printf("loaded %d customers; consuming %s and producing %s", expected, inputTopic, outputTopic)

	session, err := kgo.NewGroupTransactSession(kgo.SeedBrokers(bootstrap...),
		kgo.TransactionalID("orders-enricher-v1"), kgo.ConsumerGroup(env("CONSUMER_GROUP", "orders-enricher")),
		kgo.ConsumeTopics(inputTopic), kgo.FetchIsolationLevel(kgo.ReadCommitted()),
		kgo.ConsumeResetOffset(kgo.NewOffset().AtStart()), kgo.DisableAutoCommit())
	if err != nil {
		return fmt.Errorf("create transactional consumer: %w", err)
	}
	defer session.Close()

	for {
		if err := session.Begin(); err != nil {
			return fmt.Errorf("begin transaction: %w", err)
		}
		fetches := session.PollFetches(ctx)
		if errs := fetches.Errors(); len(errs) > 0 {
			_, endErr := session.End(ctx, kgo.TryAbort)
			return fmt.Errorf("order fetch: %v (abort: %v)", errs[0].Err, endErr)
		}
		var records []*kgo.Record
		var processErr error
		fetches.EachRecord(func(record *kgo.Record) {
			if processErr != nil {
				return
			}
			var order demov1.Order
			if err := proto.Unmarshal(record.Value, &order); err != nil {
				processErr = fmt.Errorf("decode order: %w", err)
				return
			}
			customer, ok := state.get(order.CustomerId)
			if !ok {
				processErr = fmt.Errorf("customer %q not found in CDC cache", order.CustomerId)
				return
			}
			value, err := proto.Marshal(&demov1.EnrichedOrder{Order: &order, Customer: customer})
			if err != nil {
				processErr = fmt.Errorf("encode enriched order: %w", err)
				return
			}
			records = append(records, &kgo.Record{Topic: outputTopic, Key: record.Key, Value: value})
		})
		if processErr == nil && len(records) > 0 {
			if results := session.ProduceSync(ctx, records...); results.FirstErr() != nil {
				processErr = fmt.Errorf("produce enriched orders: %w", results.FirstErr())
			}
		}
		if processErr != nil {
			_, endErr := session.End(ctx, kgo.TryAbort)
			return fmt.Errorf("process transaction: %w (abort: %v)", processErr, endErr)
		}
		if _, err := session.End(ctx, kgo.TryCommit); err != nil {
			return fmt.Errorf("commit transaction: %w", err)
		}
	}
}
