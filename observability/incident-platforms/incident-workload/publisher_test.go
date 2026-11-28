package main

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

func TestFailedAdmissionRemainsUnacknowledged(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		_, _ = io.WriteString(w, "bounded injected outage")
	}))
	defer server.Close()
	path := filepath.Join(t.TempDir(), "requests.jsonl")
	journal, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	f := Fixture{Run: "00112233445566778899aabbccddeeff", PayloadSeed: "00112233445566778899aabbccddeeff", StartMS: 1795856927000, Requests: 1, MetricPoints: 1, LogsPerRequest: 1}
	accepted, err := send(context.Background(), journal, server.URL, "metrics", f, 0, 1)
	if err != nil {
		t.Fatal(err)
	}
	if err = journal.Close(); err != nil {
		t.Fatal(err)
	}
	if accepted {
		t.Fatal("503 was classified as an acknowledgement")
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	decoder := json.NewDecoder(file)
	var intent, response RequestRecord
	if err = decoder.Decode(&intent); err != nil {
		t.Fatal(err)
	}
	if err = decoder.Decode(&response); err != nil {
		t.Fatal(err)
	}
	if intent.Event != "intent" || response.Event != "response" || response.Outcome != "unacknowledged" || response.HTTPStatus != 503 || response.RawSHA256 != intent.RawSHA256 {
		t.Fatalf("failed request lost its durable intent/response witness: %+v %+v", intent, response)
	}
}
