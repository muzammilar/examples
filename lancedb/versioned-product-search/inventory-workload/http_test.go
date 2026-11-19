package main

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func TestMissingAndNullFieldsCannotLookLikeZeroValues(t *testing.T) {
	for _, body := range []string{`{"id":"0","tenant":1,"price_cents":100,"version":0}`, `{"id":"0","tenant":1,"stock":null,"price_cents":100,"version":0}`, `{"id":"0","tenant":1,"stock":0,"price_cents":"100","version":0}`} {
		if _, err := product([]byte(body)); !errors.Is(err, errProtocol) {
			t.Fatalf("accepted invalid body %s", body)
		}
	}
	if _, err := product([]byte(`{"id":"0","tenant":1,"stock":0,"price_cents":100,"version":0}`)); err != nil {
		t.Fatal(err)
	}
}
func TestMalformedAckIsNotRetriedOrCountedAsSuccess(t *testing.T) {
	var calls atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		w.Write([]byte(`{"updated":0,"catalog_version":2,"native_attempts":1}`))
	}))
	defer server.Close()
	client := &Client{http: server.Client(), writer: server.URL}
	_, _, _, err := client.mutate(context.Background(), []Product{{ID: "0", Version: 1}})
	if !errors.Is(err, errProtocol) {
		t.Fatal("malformed ACK accepted", err)
	}
	if calls.Load() != 1 {
		t.Fatal("mutation was retried")
	}
}
func TestPointEnvelopeNeedsSnapshotIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte(`{"row":{"id":"0","tenant":1,"stock":0,"price_cents":100,"version":0}}`))
	}))
	defer server.Close()
	client := &Client{http: server.Client()}
	if _, _, _, err := client.get(context.Background(), server.URL, "0"); !errors.Is(err, errProtocol) {
		t.Fatal("missing snapshot identity accepted", err)
	}
}
