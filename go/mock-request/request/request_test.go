package request_test

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"go.uber.org/mock/gomock"

	"github.com/muzammilar/mockrequest/request"
	"github.com/muzammilar/mockrequest/request/mocks"
)

// Approach 1: a generated gomock mock of the Doer interface. No network or
// server is involved; the test decides exactly what Do returns and asserts how
// it was called.
func TestFetchWithGoMock(t *testing.T) {
	ctrl := gomock.NewController(t)
	doer := mocks.NewMockDoer(ctrl)

	doer.EXPECT().
		Do(gomock.Cond(func(r *http.Request) bool {
			return r.Method == http.MethodGet &&
				r.URL.String() == "https://example.com/hello" &&
				r.Header.Get("User-Agent") == "mock-request-example"
		})).
		Return(&http.Response{
			StatusCode: http.StatusOK,
			Body:       io.NopCloser(strings.NewReader("hello world")),
		}, nil).
		Times(1)

	page, err := request.NewFetcher(doer).Fetch(context.Background(), "https://example.com/hello")
	if err != nil {
		t.Fatalf("Fetch returned error: %v", err)
	}
	if page.StatusCode != http.StatusOK || page.Bytes != len("hello world") {
		t.Fatalf("unexpected page: %+v", page)
	}
}

// Mocks make failure paths trivial to simulate, e.g. a transport error.
func TestFetchWithGoMockTransportError(t *testing.T) {
	ctrl := gomock.NewController(t)
	doer := mocks.NewMockDoer(ctrl)

	boom := errors.New("connection refused")
	doer.EXPECT().Do(gomock.Any()).Return(nil, boom)

	_, err := request.NewFetcher(doer).Fetch(context.Background(), "https://example.com")
	if !errors.Is(err, boom) {
		t.Fatalf("expected wrapped %v, got %v", boom, err)
	}
}

// Approach 2: a real *http.Client talking to an in-process httptest server.
// This exercises the full HTTP stack (headers, status codes, bodies) without
// reaching the internet.
func TestFetchWithHTTPTestServer(t *testing.T) {
	tests := []struct {
		name    string
		status  int
		body    string
		wantErr bool
	}{
		{name: "ok", status: http.StatusOK, body: "<html>ok</html>"},
		{name: "not found", status: http.StatusNotFound, body: "missing", wantErr: true},
		{name: "server error", status: http.StatusInternalServerError, wantErr: true},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if got := r.Header.Get("User-Agent"); got != "mock-request-example" {
					t.Errorf("unexpected User-Agent %q", got)
				}
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer srv.Close()

			page, err := request.NewFetcher(srv.Client()).Fetch(context.Background(), srv.URL)
			if tc.wantErr {
				if err == nil {
					t.Fatalf("expected error, got page %+v", page)
				}
				return
			}
			if err != nil {
				t.Fatalf("Fetch returned error: %v", err)
			}
			if page.Bytes != len(tc.body) {
				t.Fatalf("got %d bytes, want %d", page.Bytes, len(tc.body))
			}
		})
	}
}

// fakeDoer is a hand-rolled test double: a plain function adapter, no
// generated code or expectations.
type fakeDoer func(*http.Request) (*http.Response, error)

func (f fakeDoer) Do(r *http.Request) (*http.Response, error) { return f(r) }

// errReader fails every Read, simulating a connection dropped mid-body.
type errReader struct{ err error }

func (e errReader) Read([]byte) (int, error) { return 0, e.err }

func TestFetchSetsUserAgent(t *testing.T) {
	var got string
	doer := fakeDoer(func(r *http.Request) (*http.Response, error) {
		got = r.Header.Get("User-Agent")
		return &http.Response{StatusCode: http.StatusOK, Body: http.NoBody}, nil
	})
	if _, err := request.NewFetcher(doer).Fetch(context.Background(), "https://example.com"); err != nil {
		t.Fatalf("Fetch returned error: %v", err)
	}
	if got != "mock-request-example" {
		t.Fatalf("User-Agent = %q, want %q", got, "mock-request-example")
	}
}

func TestFetchBodyReadError(t *testing.T) {
	readErr := errors.New("connection reset")
	doer := fakeDoer(func(*http.Request) (*http.Response, error) {
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(errReader{readErr})}, nil
	})
	_, err := request.NewFetcher(doer).Fetch(context.Background(), "https://example.com")
	if !errors.Is(err, readErr) {
		t.Fatalf("expected wrapped %v, got %v", readErr, err)
	}
}

func TestFetchInvalidURL(t *testing.T) {
	doer := fakeDoer(func(*http.Request) (*http.Response, error) {
		t.Fatal("Do must not be called for an invalid URL")
		return nil, nil
	})
	if _, err := request.NewFetcher(doer).Fetch(context.Background(), "://bad-url"); err == nil {
		t.Fatal("expected error for invalid URL")
	}
}

func TestFetchContextTimeout(t *testing.T) {
	release := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select { // block until the client gives up or the test ends
		case <-r.Context().Done():
		case <-release:
		}
	}))
	defer srv.Close()
	defer close(release)

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	_, err := request.NewFetcher(srv.Client()).Fetch(ctx, srv.URL)
	if !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected context.DeadlineExceeded, got %v", err)
	}
}

func TestFetchContextCanceled(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	doer := fakeDoer(func(r *http.Request) (*http.Response, error) {
		return nil, r.Context().Err() // a context-aware transport returns the ctx error
	})
	_, err := request.NewFetcher(doer).Fetch(ctx, "https://example.com")
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("expected context.Canceled, got %v", err)
	}
}

func TestNewFetcherNilUsesDefaultClient(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "ok")
	}))
	defer srv.Close()
	page, err := request.NewFetcher(nil).Fetch(context.Background(), srv.URL)
	if err != nil || page.Bytes != 2 {
		t.Fatalf("got page %+v, err %v", page, err)
	}
}
