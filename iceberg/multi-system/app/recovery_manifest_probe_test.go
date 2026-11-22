package main

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
)

type manifestProbeTransport func(*http.Request) (*http.Response, error)

func (f manifestProbeTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

const manifestProbeTestPrefix = "/warehouse/lab/recovery_fixture-5742e029-c49e-4e57-8832-ddc6495e1e8d/"

func TestManifestProbeForwardsSignedHostEscapedPathAndQuery(t *testing.T) {
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, false, func(any) error { return nil })
	defer transport.CloseIdleConnections()
	if transport.Proxy != nil {
		t.Fatal("ambient HTTP proxy is enabled")
	}
	path := manifestProbeTestPrefix + "data/a%20b.parquet?versionId=x%2By&partNumber=1"
	request := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:12345"+path, nil)
	request.Header.Set("Authorization", "AWS4-HMAC-SHA256 SignedHeaders=host;x-amz-date")
	p.forward.Transport = manifestProbeTransport(func(r *http.Request) (*http.Response, error) {
		if r.URL.Scheme != "http" || r.URL.Host != "storage:8333" || r.Host != request.Host || r.URL.EscapedPath()+"?"+r.URL.RawQuery != path || r.Header.Get("Authorization") != request.Header.Get("Authorization") {
			t.Fatalf("signed request changed: host=%q URL=%s", r.Host, r.URL)
		}
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("body"))}, nil
	})
	response := httptest.NewRecorder()
	p.ServeHTTP(response, request)
	if response.Code != 200 || response.Body.String() != "body" {
		t.Fatalf("wrong proxy response: %d %s", response.Code, response.Body.String())
	}
}

func TestManifestProbeRejectsMutationsForeignPathsAndTraversal(t *testing.T) {
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, true, func(any) error { return nil })
	defer transport.CloseIdleConnections()
	p.forward.Transport = manifestProbeTransport(func(*http.Request) (*http.Response, error) { t.Fatal("forbidden request forwarded"); return nil, nil })
	for _, test := range []struct{ method, path string }{
		{http.MethodPut, manifestProbeTestPrefix + "data/file.parquet"},
		{http.MethodPost, manifestProbeTestPrefix + "metadata/m.avro"},
		{http.MethodDelete, manifestProbeTestPrefix + "data/file.parquet"},
		{http.MethodGet, "/warehouse/lab/another-table/metadata/m.avro"},
		{http.MethodGet, manifestProbeTestPrefix + "metadata/%2e%2e/private.avro"},
	} {
		response := httptest.NewRecorder()
		p.ServeHTTP(response, httptest.NewRequest(test.method, "http://local"+test.path, nil))
		if response.Code != http.StatusForbidden {
			t.Fatalf("%s %s: %d", test.method, test.path, response.Code)
		}
	}
}

func TestManifestProbeDropsOnlyDataManifestGETAfterWitness(t *testing.T) {
	var mu sync.Mutex
	var actions []string
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, true, func(value any) error {
		mu.Lock()
		defer mu.Unlock()
		actions = append(actions, value.(map[string]any)["action"].(string))
		return nil
	})
	defer transport.CloseIdleConnections()
	p.forward.Transport = manifestProbeTransport(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: make(http.Header), Body: io.NopCloser(strings.NewReader("fixture"))}, nil
	})
	server := httptest.NewServer(p)
	defer server.Close()
	client := &http.Client{Transport: &http.Transport{Proxy: nil, DisableKeepAlives: true}}
	for _, test := range []struct{ method, path string }{
		{http.MethodGet, "metadata/v1.metadata.json"},
		{http.MethodGet, "metadata/snap-123.avro"},
		{http.MethodGet, "data/file.parquet"},
		{http.MethodHead, "metadata/manifest-m0.avro"},
	} {
		request, _ := http.NewRequest(test.method, server.URL+manifestProbeTestPrefix+test.path, nil)
		response, err := client.Do(request)
		if err != nil {
			t.Fatal(err)
		}
		_ = response.Body.Close()
	}
	if response, err := client.Get(server.URL + manifestProbeTestPrefix + "metadata/manifest-m0.avro"); err == nil {
		_ = response.Body.Close()
		t.Fatal("fault returned HTTP headers instead of closing the connection")
	}
	server.Close()
	mu.Lock()
	defer mu.Unlock()
	if len(actions) != 6 || actions[4] != "manifest-connection-close-intent" || actions[5] != "manifest-connection-closed" || p.dropped != 1 {
		t.Fatalf("fault witness/order differs: %v dropped=%d", actions, p.dropped)
	}
}

func TestManifestProbeWitnessFailurePreventsForwarding(t *testing.T) {
	original := errors.New("witness sync failed")
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, false, func(any) error { return original })
	defer transport.CloseIdleConnections()
	p.forward.Transport = manifestProbeTransport(func(*http.Request) (*http.Response, error) { t.Fatal("unwitnessed request forwarded"); return nil, nil })
	response := httptest.NewRecorder()
	p.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://local"+manifestProbeTestPrefix+"metadata/v1.metadata.json", nil))
	if response.Code != 500 || !errors.Is(p.failure, original) {
		t.Fatalf("witness failure lost: %d %v", response.Code, p.failure)
	}
}

func TestManifestProbeBackendErrorIsRecordedAndReturnedAs502(t *testing.T) {
	var last string
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, false, func(value any) error { last = value.(map[string]any)["action"].(string); return nil })
	defer transport.CloseIdleConnections()
	p.forward.Transport = manifestProbeTransport(func(*http.Request) (*http.Response, error) { return nil, errors.New("local backend unavailable") })
	response := httptest.NewRecorder()
	p.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://local"+manifestProbeTestPrefix+"metadata/v1.metadata.json", nil))
	if response.Code != 502 || last != "backend-error" {
		t.Fatalf("backend error was not recorded: %d %q", response.Code, last)
	}
}

func TestManifestProbeDoesNotExposeBackendRedirectToReader(t *testing.T) {
	p, transport := newManifestProbeProxy(manifestProbeTestPrefix, false, func(any) error { return nil })
	defer transport.CloseIdleConnections()
	p.forward.Transport = manifestProbeTransport(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: 307, Header: http.Header{"Location": {"https://outside.invalid/file"}}, Body: io.NopCloser(strings.NewReader("redirect"))}, nil
	})
	response := httptest.NewRecorder()
	p.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "http://local"+manifestProbeTestPrefix+"metadata/v1.metadata.json", nil))
	if response.Code != 502 || response.Header().Get("Location") != "" {
		t.Fatal("backend redirect escaped read-only shim")
	}
}
