package main

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
)

func TestOracleRejectsInvalidResults(t *testing.T) {
	for _, tc := range []struct {
		name, body string
		status     int
	}{
		{"missing ID", "9\t9\t55\t0", 200},
		{"duplicate ID", "10\t9\t55\t0", 200},
		{"wrong amount", "10\t10\t54\t0", 200},
		{"invalid row", "10\t10\t55\t1", 200},
		{"server error", "unavailable", 503},
	} {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.WriteHeader(tc.status); fmt.Fprint(w, tc.body) }))
			defer server.Close()
			t.Setenv("CLICKHOUSE_URL", server.URL)
			if err := check(context.Background(), "default.events", 0, 10, 10); err == nil {
				t.Fatal("invalid oracle response was accepted")
			}
		})
	}
}

func TestAmbiguousAppendIsNotReplayed(t *testing.T) {
	var commits atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		if !strings.HasPrefix(string(body), "INSERT INTO") {
			t.Errorf("unexpected follow-up request: %s", body)
		}
		commits.Add(1) // Simulate a commit followed by losing the acknowledgement.
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}))
	defer server.Close()
	t.Setenv("CLICKHOUSE_URL", server.URL)
	err := run(context.Background(), "append")
	if err == nil || !strings.Contains(err.Error(), "unresolved append") {
		t.Fatalf("expected ambiguous append error, got %v", err)
	}
	if got := commits.Load(); got != 1 {
		t.Fatalf("ambiguous append was replayed: %d commits", got)
	}
}
