package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

type recoveryCatalogTransport func(*http.Request) (*http.Response, error)

func (f recoveryCatalogTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	return f(r)
}

func TestRecoveryCatalogPreservesSnapshotInteger(t *testing.T) {
	for _, snapshot := range []int64{9007199254740993, 2696719651453291501, 9223372036854775806} {
		t.Run(fmt.Sprint(snapshot), func(t *testing.T) {
			body := fmt.Sprintf(`{"metadata-location":"s3://warehouse/lab/table/metadata/v249.metadata.json","metadata":{"table-uuid":"21870514-0f8c-4195-a836-dd83cf40bce7","format-version":2,"current-snapshot-id":%d,"snapshots":[{"snapshot-id":%d}]}}`, snapshot, snapshot)
			root := t.TempDir()
			writeRecoveryCatalogFixture(t, root, body)
			state, err := recoveryCatalogState(root, "catalog")
			if err != nil || state.Snapshot != snapshot {
				t.Fatalf("snapshot round trip: got %+v, %v; want %d", state, err, snapshot)
			}
			data, err := os.ReadFile(filepath.Join(root, "catalog.json"))
			if err != nil {
				t.Fatal(err)
			}
			var record struct {
				BodyHash string          `json:"body_sha256"`
				Table    json.RawMessage `json:"load_table"`
			}
			if err = json.Unmarshal(data, &record); err != nil {
				t.Fatal(err)
			}
			if record.BodyHash != recoveryDigest([]byte(body)) || strings.Count(string(record.Table), fmt.Sprint(snapshot)) != 2 {
				t.Fatalf("raw catalog integer/hash changed: %s", data)
			}
		})
	}
}

func TestRecoveryCatalogMalformedJSONIsUnavailable(t *testing.T) {
	root := t.TempDir()
	writeRecoveryCatalogFixture(t, root, `{"metadata":`)
	if _, err := recoveryCatalogState(root, "catalog"); err == nil {
		t.Fatal("malformed catalog must not produce a usable snapshot")
	}
	data, err := os.ReadFile(filepath.Join(root, "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	var record struct {
		Available bool            `json:"available"`
		Error     string          `json:"error"`
		Table     json.RawMessage `json:"load_table"`
	}
	if err = json.Unmarshal(data, &record); err != nil || record.Available || record.Error == "" || record.Table != nil {
		t.Fatalf("malformed catalog was not retained as unavailable: %s; %v", data, err)
	}
}

func writeRecoveryCatalogFixture(t *testing.T, root, body string) {
	t.Helper()
	previous := http.DefaultTransport
	t.Cleanup(func() { http.DefaultTransport = previous })
	calls := 0
	http.DefaultTransport = recoveryCatalogTransport(func(r *http.Request) (*http.Response, error) {
		calls++
		response := body
		switch {
		case r.Method == http.MethodPost && r.URL.String() == "http://storage:8181/v1/oauth/tokens":
			response = `{"access_token":"isolated-test-token"}`
		case r.Method == http.MethodGet && r.URL.String() == "http://storage:8181/v1/namespaces/lab/tables/recovery_test" && r.Header.Get("Authorization") == "Bearer isolated-test-token":
		default:
			t.Fatalf("unexpected catalog request: %s %s", r.Method, r.URL)
		}
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(response)), Header: make(http.Header)}, nil
	})
	if err := recoveryCatalogRecord(context.Background(), root, recoveryConfig{RunID: "36e77aab-4a09-4bf8-8192-2b4ed20a7696", Table: "recovery_test"}, "fixture-config-hash", "catalog"); err != nil {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatalf("expected explicit OAuth and catalog requests, got %d", calls)
	}
}
