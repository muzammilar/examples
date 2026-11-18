package main

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func fixtureForTest() *Fixture {
	return &Fixture{Products: []Product{{ID: "0", Tenant: 1, Stock: 10, PriceCents: 100}, {ID: "1", Tenant: 2, Stock: 20, PriceCents: 200}}, Queries: []Query{{Text: "example"}}, Vectors: [][]float32{{1, 0}}}
}

func TestPutIsNotReplayedAfterApplicationAndDisconnect(t *testing.T) {
	var applied atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			io.WriteString(w, "ok")
			return
		}
		io.Copy(io.Discard, r.Body)
		applied.Add(1)
		connection, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		connection.Close()
	}))
	defer server.Close()
	client, _ := newClient("cluster")
	if _, err := client.request(context.Background(), server.URL, http.MethodGet, "/warm", nil); err != nil {
		t.Fatal(err)
	}
	if _, err := client.request(context.Background(), server.URL, http.MethodPut, "/mutation", []byte("body")); err == nil {
		t.Fatal("expected ambiguous disconnect")
	}
	if applied.Load() != 1 {
		t.Fatalf("mutation applied %d times", applied.Load())
	}
}

func TestUpdateIdentityAndIgnoredHeadersAreNotAcknowledgements(t *testing.T) {
	good := Response{Header: make(http.Header), Body: []byte(`{"id":"id:catalog:product::0","pathId":"/document/v1/catalog/product/docid/0"}`)}
	if err := validateUpdate(good, "0"); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"X-Vespa-Ignored-Operation", "X-Vespa-Ignored-Fields"} {
		bad := good
		bad.Header = make(http.Header)
		bad.Header.Set(name, "true")
		if err := validateUpdate(bad, "0"); !errors.Is(err, errProtocol) {
			t.Fatalf("ignored header accepted: %s", name)
		}
	}
	for _, body := range []string{`{}`, `{"id":"id:catalog:product::1","pathId":"/document/v1/catalog/product/docid/0"}`, `{"id":`} {
		bad := good
		bad.Body = []byte(body)
		if err := validateUpdate(bad, "0"); !errors.Is(err, errProtocol) {
			t.Fatalf("invalid acknowledgement accepted: %s", body)
		}
	}
}

func TestConditionalPerDocumentOutcomesKeepPartialSuccess(t *testing.T) {
	root := t.TempDir()
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/document/v1/catalog/product/docid/")
		if r.Method != http.MethodPut || r.URL.Query().Get("create") != "false" || !strings.Contains(r.URL.Query().Get("condition"), "product.version < 1") {
			t.Errorf("unsafe update: %s %s", r.Method, r.URL)
		}
		var body struct {
			Fields map[string]map[string]int `json:"fields"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		if len(body.Fields) != 3 || body.Fields["version"]["assign"] != 1 {
			t.Errorf("unexpected updates: %+v", body.Fields)
		}
		if calls.Add(1) == 2 {
			if err := os.WriteFile(filepath.Join(root, "stop-writer"), nil, 0600); err != nil {
				t.Error(err)
			}
		}
		if id == "1" {
			http.Error(w, "condition failed", http.StatusPreconditionFailed)
			return
		}
		json.NewEncoder(w).Encode(map[string]string{"id": "id:catalog:product::" + id, "pathId": r.URL.Path})
	}))
	defer server.Close()
	client, _ := newClient("single")
	client.URLs = []string{server.URL}
	journal, err := openJournal(filepath.Join(root, "writer.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	defer journal.file.Close()
	if err := writeInventory(context.Background(), root, fixtureForTest(), client, journal, 1, 2); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(root, "writer.json"))
	if err != nil {
		t.Fatal(err)
	}
	var result struct {
		ACK       int  `json:"acknowledged_rows"`
		Rejected  int  `json:"rejected_rows"`
		Ambiguous int  `json:"ambiguous_rows"`
		Stopped   bool `json:"stopped_by_controller"`
	}
	if err := json.Unmarshal(data, &result); err != nil {
		t.Fatal(err)
	}
	if result.ACK != 1 || result.Rejected != 1 || result.Ambiguous != 0 || !result.Stopped || calls.Load() != 2 {
		t.Fatalf("wrong partial outcome: %+v calls=%d", result, calls.Load())
	}
}

func TestPartialSearchNeedsKnownValuesAndOverlappingWrite(t *testing.T) {
	fixture := fixtureForTest()
	next, _ := fixture.product("0", 1)
	tail := &Tail{ACK: map[string]int{"0": 1}, ACKTimes: map[string]float64{"0": 110}, PriorACK: map[string]int{}, Attempts: map[string]map[int]bool{"0": {1: true}}, Windows: map[string]map[int]*writeWindow{"0": {1: {Row: next, Start: 100, End: 110, Outcome: "ack"}}}}
	mixed := next
	mixed.Stock = fixture.Products[0].Stock
	if partial, err := tail.partialSearch(fixture, mixed, 105, 106); err != nil || !partial {
		t.Fatalf("valid in-progress view rejected: partial=%v err=%v", partial, err)
	}
	if err := tail.checked(fixture, mixed); err == nil {
		t.Fatal("atomic document GET accepted mixed fields")
	}
	if _, err := tail.partialSearch(fixture, mixed, 120, 121); err == nil {
		t.Fatal("partial fields accepted outside update interval")
	}
	for _, bad := range []Product{{ID: "0", Tenant: 2, Stock: mixed.Stock, PriceCents: mixed.PriceCents, Version: 1}, {ID: "0", Tenant: 1, Stock: 42, PriceCents: 101, Version: 1}, {ID: "0", Tenant: 1, Stock: 10, PriceCents: 199, Version: 1}} {
		if _, err := tail.partialSearch(fixture, bad, 105, 106); err == nil {
			t.Fatalf("unknown/leaked value accepted: %+v", bad)
		}
	}
	if tail.searchFloor("0", 105) != 0 || tail.searchFloor("0", 120) != 1 {
		t.Fatal("query floor included an acknowledgement after query start")
	}
}

func TestCoverageErrorsAreFailedQueriesAndMalformedIdentityIsProtocolFailure(t *testing.T) {
	cases := []struct {
		name, body string
		protocol   bool
	}{
		{"coverage", `{"root":{"coverage":{"coverage":99,"full":false}}}`, false},
		{"short", `{"root":{"coverage":{"coverage":100,"full":true},"children":[]}}`, false},
		{"missing pid", `{"root":{"coverage":{"coverage":100,"full":true},"children":[{"fields":{"tenant":1}}]}}`, true},
	}
	for _, test := range cases {
		t.Run(test.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, test.body) }))
			defer server.Close()
			client, _ := newClient("single")
			client.URLs = []string{server.URL}
			_, _, err := search(context.Background(), client, fixtureForTest(), 0, 0, "dense", 1)
			if err == nil || errors.Is(err, errProtocol) != test.protocol {
				t.Fatalf("unexpected classification: %v", err)
			}
		})
	}
}
