package request

import (
	"io"
	"net/http"
	"strconv"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
)

// Metrics holds the Prometheus collectors recorded by InstrumentedDoer.
//
// Every series is labelled with the request URL (scheme, host and path; the
// query string is dropped to keep label cardinality bounded).
type Metrics struct {
	requests    *prometheus.CounterVec   // httpmock_requests_total{url,code}
	errors      *prometheus.CounterVec   // httpmock_request_errors_total{url}
	duration    *prometheus.HistogramVec // httpmock_request_duration_seconds{url}
	size        *prometheus.HistogramVec // httpmock_response_size_bytes{url}
	lastSuccess *prometheus.GaugeVec     // httpmock_last_success_timestamp_seconds{url}

	// now is the clock used for durations and the last-success timestamp.
	// Tests replace it to get deterministic values.
	now func() time.Time
}

// NewMetrics creates the collectors and registers them with reg.
func NewMetrics(reg prometheus.Registerer) *Metrics {
	m := &Metrics{
		requests: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "httpmock_requests_total",
			Help: "HTTP requests that received a response, by URL and status code.",
		}, []string{"url", "code"}),
		errors: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "httpmock_request_errors_total",
			Help: "HTTP requests that failed without a response (transport errors, timeouts), by URL.",
		}, []string{"url"}),
		duration: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "httpmock_request_duration_seconds",
			Help:    "Time until the response headers arrived (or the request failed), by URL.",
			Buckets: []float64{.005, .01, .025, .05, .1, .25, .5, 1, 2.5, 5, 10},
		}, []string{"url"}),
		size: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "httpmock_response_size_bytes",
			Help:    "Bytes read from the response body before it was closed, by URL.",
			Buckets: prometheus.ExponentialBuckets(64, 4, 8), // 64B .. 1MiB
		}, []string{"url"}),
		lastSuccess: prometheus.NewGaugeVec(prometheus.GaugeOpts{
			Name: "httpmock_last_success_timestamp_seconds",
			Help: "Unix time of the last 2xx response, by URL.",
		}, []string{"url"}),
		now: time.Now,
	}
	reg.MustRegister(m.requests, m.errors, m.duration, m.size, m.lastSuccess)
	return m
}

// InstrumentedDoer is a Doer decorator: it wraps another Doer and records
// Prometheus metrics about every call. Because it only depends on the Doer
// interface, it can wrap a real *http.Client in production and a gomock mock
// or a fake in tests.
type InstrumentedDoer struct {
	next    Doer
	metrics *Metrics
}

// NewInstrumentedDoer wraps next so that each Do call is recorded in m.
func NewInstrumentedDoer(next Doer, m *Metrics) *InstrumentedDoer {
	return &InstrumentedDoer{next: next, metrics: m}
}

// Do performs the request with the wrapped Doer and records its outcome.
func (d *InstrumentedDoer) Do(req *http.Request) (*http.Response, error) {
	m := d.metrics
	url := urlLabel(req)
	start := m.now()

	resp, err := d.next.Do(req)
	m.duration.WithLabelValues(url).Observe(m.now().Sub(start).Seconds())
	if err != nil {
		m.errors.WithLabelValues(url).Inc()
		return resp, err
	}

	m.requests.WithLabelValues(url, strconv.Itoa(resp.StatusCode)).Inc()
	if resp.StatusCode >= 200 && resp.StatusCode <= 299 {
		m.lastSuccess.WithLabelValues(url).Set(float64(m.now().UnixNano()) / 1e9)
	}
	if resp.Body != nil {
		// the body is streamed, so its size is only known once the caller closes it
		resp.Body = &countingBody{ReadCloser: resp.Body, observe: m.size.WithLabelValues(url).Observe}
	}
	return resp, nil
}

// urlLabel returns the request URL without its query string or fragment.
func urlLabel(req *http.Request) string {
	if req.URL == nil {
		return ""
	}
	u := *req.URL
	u.RawQuery, u.Fragment, u.RawFragment, u.User = "", "", "", nil
	return u.String()
}

// countingBody counts the bytes read from a response body and reports the
// total once, when the body is closed.
type countingBody struct {
	io.ReadCloser
	n       int64
	once    sync.Once
	observe func(float64)
}

func (b *countingBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.n += int64(n)
	return n, err
}

func (b *countingBody) Close() error {
	b.once.Do(func() { b.observe(float64(b.n)) })
	return b.ReadCloser.Close()
}
