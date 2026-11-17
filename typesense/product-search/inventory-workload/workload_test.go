package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

func TestImportOutcomesPreservePartialAcknowledgements(t *testing.T) {
	rows := []Product{{ID: "0", Version: 1}, {ID: "1", Version: 1}}
	tests := []struct {
		name, body               string
		complete                 bool
		ack, rejected, ambiguous int
		fails                    bool
	}{
		{"partial rejection", "{\"success\":true,\"id\":\"0\"}\n{\"success\":false,\"error\":\"rejected\"}", true, 1, 1, 0, false},
		{"truncated transport", "{\"success\":true,\"id\":\"0\"}\n{\"success\":", false, 1, 0, 1, false},
		{"short complete body", "{\"success\":true,\"id\":\"0\"}", true, 1, 0, 1, true},
		{"wrong ID", "{\"success\":true,\"id\":\"9\"}", true, 0, 0, 2, true},
		{"missing success", "{\"id\":\"0\"}", true, 0, 0, 2, true},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			result, err := parseImport([]byte(test.body), rows, test.complete)
			if (err != nil) != test.fails || len(result.ACK) != test.ack || len(result.Rejected) != test.rejected || len(result.Ambiguous) != test.ambiguous {
				t.Fatalf("result=%+v err=%v", result, err)
			}
		})
	}
}

func TestMutationIsNotReplayedAfterServerAppliesAndDisconnects(t *testing.T) {
	var applied atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			io.WriteString(w, "ok")
			return
		}
		io.Copy(io.Discard, r.Body)
		applied.Add(1)
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}))
	defer server.Close()
	client, err := newClient("cluster")
	if err != nil {
		t.Fatal(err)
	}
	if _, _, err := client.request(context.Background(), server.URL, http.MethodGet, "/warm", nil); err != nil {
		t.Fatal(err)
	}
	if _, _, err := client.request(context.Background(), server.URL, http.MethodPost, "/mutation", []byte("body")); err == nil {
		t.Fatal("expected ambiguous transport failure")
	}
	if applied.Load() != 1 {
		t.Fatalf("mutation applied %d times", applied.Load())
	}
}

func TestReadFailoverRetainsFailedAttempt(t *testing.T) {
	bad := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { http.Error(w, "not ready", 503) }))
	defer bad.Close()
	good := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { io.WriteString(w, "ok") }))
	defer good.Close()
	client, err := newClient("cluster")
	if err != nil {
		t.Fatal(err)
	}
	client.Groups = [][]string{{bad.URL, good.URL}}
	result, err := client.read(context.Background(), 0, http.MethodGet, "/read", nil)
	if err != nil || result.Attempts != 2 || len(result.Failures) != 1 || string(result.Body) != "ok" {
		t.Fatalf("result=%+v err=%v", result, err)
	}
}

func TestJournalTailWaitsForCompleteRecordAndRejectsDuplicateOutcome(t *testing.T) {
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "history.json"), []byte(`{"ack_versions":{},"ack_times":{},"attempted_versions":{}}`), 0644); err != nil {
		t.Fatal(err)
	}
	tail, err := loadHistory(root)
	if err != nil {
		t.Fatal(err)
	}
	defer func() {
		if tail.file != nil {
			tail.file.Close()
		}
	}()
	row := Product{ID: "0", Version: 1}
	intent, err := json.Marshal(Event{Stage: "attempt", Rows: []Product{row}})
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.OpenFile(filepath.Join(root, "writer.jsonl"), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	file.Write(intent[:len(intent)/2])
	if err := tail.consume(); err != nil || len(tail.Attempts) != 0 {
		t.Fatalf("partial record consumed: %v", err)
	}
	file.Write(append(intent[len(intent)/2:], '\n'))
	json.NewEncoder(file).Encode(Event{Stage: "ack", Time: 1, Rows: []Product{row}})
	if err := tail.consume(); err != nil || tail.ACK["0"] != 1 {
		t.Fatalf("complete records not consumed: %v", err)
	}
	json.NewEncoder(file).Encode(Event{Stage: "ambiguous", Rows: []Product{row}})
	if err := tail.consume(); err == nil || !strings.Contains(err.Error(), "duplicate outcome") {
		t.Fatalf("duplicate outcome accepted: %v", err)
	}
}

func TestObservedVersionMustHaveIntentAndCanonicalPayload(t *testing.T) {
	fixture := &Fixture{Products: []Product{{ID: "0", Tenant: 1, PriceCents: 100, Stock: 3}}}
	tail := &Tail{Attempts: map[string]map[int]bool{"0": {1: true}}}
	value, err := fixture.product("0", 1)
	if err != nil {
		t.Fatal(err)
	}
	if err := tail.checked(fixture, value); err != nil {
		t.Fatal(err)
	}
	value.Tenant = 2
	if err := tail.checked(fixture, value); err == nil {
		t.Fatal("cross-tenant payload accepted")
	}
	value, _ = fixture.product("0", 2)
	if err := tail.checked(fixture, value); err == nil {
		t.Fatal("unknown version accepted")
	}
}

func TestJournalCannotOverwriteEarlierWitness(t *testing.T) {
	path := filepath.Join(t.TempDir(), "writer.jsonl")
	if err := os.WriteFile(path, []byte("retained witness"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := openJournal(path); err == nil {
		t.Fatal("existing witness reopened")
	}
	data, err := os.ReadFile(path)
	if err != nil || string(data) != "retained witness" {
		t.Fatalf("witness changed: %q %v", data, err)
	}
}
