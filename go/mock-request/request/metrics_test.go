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

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/testutil"
	"go.uber.org/mock/gomock"

	"github.com/muzammilar/mockrequest/request"
	"github.com/muzammilar/mockrequest/request/mocks"
)

// newTestMetrics returns metrics on a private registry with a clock that
// starts at Unix 1700000000 and advances 250ms on every call. The decorator
// reads the clock before Do, after Do and (on success) for the timestamp, so a
// successful request takes exactly 0.25s and succeeds at 1700000000.5.
func newTestMetrics() (*prometheus.Registry, *request.Metrics) {
	reg := prometheus.NewRegistry()
	m := request.NewMetrics(reg)
	t := time.Unix(1700000000, 0)
	request.SetClock(m, func() time.Time {
		now := t
		t = t.Add(250 * time.Millisecond)
		return now
	})
	return reg, m
}

func response(code int, body string) *http.Response {
	return &http.Response{StatusCode: code, Body: io.NopCloser(strings.NewReader(body))}
}

// The decorator is tested with the same generated gomock mock as Fetcher: the
// mock stands in for the network, and the assertions are on the metrics.
func TestInstrumentedDoerSuccess(t *testing.T) {
	ctrl := gomock.NewController(t)
	inner := mocks.NewMockDoer(ctrl)
	reg, m := newTestMetrics()

	// the decorator must hand the caller's request to the wrapped Doer untouched
	inner.EXPECT().
		Do(gomock.Cond(func(r *http.Request) bool {
			return r.URL.String() == "https://example.com/hello?page=2" &&
				r.Header.Get("User-Agent") == "mock-request-example"
		})).
		Return(response(http.StatusOK, "hello world"), nil).
		Times(1)

	f := request.NewFetcher(request.NewInstrumentedDoer(inner, m))
	if _, err := f.Fetch(context.Background(), "https://example.com/hello?page=2"); err != nil {
		t.Fatalf("Fetch returned error: %v", err)
	}

	// the query string is dropped from the url label
	want := `
# HELP httpmock_last_success_timestamp_seconds Unix time of the last 2xx response, by URL.
# TYPE httpmock_last_success_timestamp_seconds gauge
httpmock_last_success_timestamp_seconds{url="https://example.com/hello"} 1.7000000005e+09
# HELP httpmock_request_errors_total HTTP requests that failed without a response (transport errors, timeouts), by URL.
# TYPE httpmock_request_errors_total counter
# HELP httpmock_requests_total HTTP requests that received a response, by URL and status code.
# TYPE httpmock_requests_total counter
httpmock_requests_total{code="200",url="https://example.com/hello"} 1
`
	if err := testutil.GatherAndCompare(reg, strings.NewReader(want),
		"httpmock_requests_total", "httpmock_request_errors_total", "httpmock_last_success_timestamp_seconds"); err != nil {
		t.Fatal(err)
	}
	assertHistogram(t, reg, "httpmock_request_duration_seconds", 1, 0.25)
	assertHistogram(t, reg, "httpmock_response_size_bytes", 1, float64(len("hello world")))
}

func TestInstrumentedDoerNon2xx(t *testing.T) {
	ctrl := gomock.NewController(t)
	inner := mocks.NewMockDoer(ctrl)
	reg, m := newTestMetrics()

	inner.EXPECT().Do(gomock.Any()).Return(response(http.StatusServiceUnavailable, "down"), nil).Times(1)
	// DoAndReturn builds a fresh response (and body) for each call
	inner.EXPECT().Do(gomock.Any()).DoAndReturn(func(*http.Request) (*http.Response, error) {
		return response(http.StatusNotFound, "missing"), nil
	}).Times(2)

	f := request.NewFetcher(request.NewInstrumentedDoer(inner, m))
	for range 3 {
		if _, err := f.Fetch(context.Background(), "https://example.com/x"); err == nil {
			t.Fatal("Fetch returned nil error for a non-2xx status")
		}
	}

	want := `
# HELP httpmock_requests_total HTTP requests that received a response, by URL and status code.
# TYPE httpmock_requests_total counter
httpmock_requests_total{code="404",url="https://example.com/x"} 2
httpmock_requests_total{code="503",url="https://example.com/x"} 1
`
	if err := testutil.GatherAndCompare(reg, strings.NewReader(want), "httpmock_requests_total"); err != nil {
		t.Fatal(err)
	}
	// a non-2xx response is not a success, and not a transport error either
	if n, _ := testutil.GatherAndCount(reg, "httpmock_last_success_timestamp_seconds", "httpmock_request_errors_total"); n != 0 {
		t.Fatalf("got %d last-success/error series, want 0", n)
	}
	assertHistogram(t, reg, "httpmock_response_size_bytes", 3, float64(len("down")+2*len("missing")))
}

func TestInstrumentedDoerTransportError(t *testing.T) {
	ctrl := gomock.NewController(t)
	inner := mocks.NewMockDoer(ctrl)
	reg, m := newTestMetrics()

	boom := errors.New("connection refused")
	inner.EXPECT().Do(gomock.Any()).Return(nil, boom).Times(1)

	_, err := request.NewInstrumentedDoer(inner, m).Do(httptest.NewRequest(http.MethodGet, "http://target:8081/slow", nil))
	if !errors.Is(err, boom) {
		t.Fatalf("Do error = %v, want %v", err, boom)
	}

	want := `
# HELP httpmock_request_errors_total HTTP requests that failed without a response (transport errors, timeouts), by URL.
# TYPE httpmock_request_errors_total counter
httpmock_request_errors_total{url="http://target:8081/slow"} 1
`
	if err := testutil.GatherAndCompare(reg, strings.NewReader(want), "httpmock_request_errors_total"); err != nil {
		t.Fatal(err)
	}
	if n, _ := testutil.GatherAndCount(reg, "httpmock_requests_total", "httpmock_response_size_bytes"); n != 0 {
		t.Fatalf("got %d request/size series after a transport error, want 0", n)
	}
	// failed requests are still timed
	assertHistogram(t, reg, "httpmock_request_duration_seconds", 1, 0.25)
}

// A hand-rolled fake works just as well as the gomock mock. Here the body
// fails half-way through: the bytes read before the failure are still counted,
// exactly once, even though Close is called twice.
func TestInstrumentedDoerCountsPartialBody(t *testing.T) {
	reg, m := newTestMetrics()
	doer := request.NewInstrumentedDoer(fakeDoer(func(*http.Request) (*http.Response, error) {
		body := io.MultiReader(strings.NewReader("12345"), errReader{errors.New("reset")})
		return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(body)}, nil
	}), m)

	resp, err := doer.Do(httptest.NewRequest(http.MethodGet, "https://example.com/", nil))
	if err != nil {
		t.Fatalf("Do returned error: %v", err)
	}
	if _, err := io.ReadAll(resp.Body); err == nil {
		t.Fatal("ReadAll returned nil error, want the body error")
	}
	_ = resp.Body.Close()
	_ = resp.Body.Close()

	assertHistogram(t, reg, "httpmock_response_size_bytes", 1, 5)
}

// assertHistogram checks the sample count and sum of the single series of a histogram.
func assertHistogram(t *testing.T, reg *prometheus.Registry, name string, count uint64, sum float64) {
	t.Helper()
	mfs, err := reg.Gather()
	if err != nil {
		t.Fatal(err)
	}
	for _, mf := range mfs {
		if mf.GetName() != name {
			continue
		}
		if len(mf.GetMetric()) != 1 {
			t.Fatalf("%s has %d series, want 1", name, len(mf.GetMetric()))
		}
		h := mf.GetMetric()[0].GetHistogram()
		if h.GetSampleCount() != count || h.GetSampleSum() != sum {
			t.Fatalf("%s count=%d sum=%v, want count=%d sum=%v", name, h.GetSampleCount(), h.GetSampleSum(), count, sum)
		}
		return
	}
	t.Fatalf("%s not found", name)
}
