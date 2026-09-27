package request_test

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/prometheus/client_golang/prometheus"
	"go.uber.org/mock/gomock"

	"github.com/muzammilar/mockrequest/request"
	"github.com/muzammilar/mockrequest/request/mocks"
)

const benchBody = "<html><body>hello, benchmark</body></html>"

func okResponse() *http.Response {
	return &http.Response{StatusCode: http.StatusOK, Body: io.NopCloser(strings.NewReader(benchBody))}
}

// BenchmarkFetch compares the per-call cost of the test doubles (and of the metrics decorator) for the
// same Fetcher.Fetch call.
func BenchmarkFetch(b *testing.B) {
	ctx := context.Background()
	const url = "http://example.test/"

	b.Run("gomock", func(b *testing.B) {
		ctrl := gomock.NewController(b)
		doer := mocks.NewMockDoer(ctrl)
		doer.EXPECT().Do(gomock.Any()).DoAndReturn(func(*http.Request) (*http.Response, error) {
			return okResponse(), nil
		}).AnyTimes()
		f := request.NewFetcher(doer)

		b.ReportAllocs()
		b.ResetTimer()
		for i := 0; i < b.N; i++ {
			if _, err := f.Fetch(ctx, url); err != nil {
				b.Fatal(err)
			}
		}
	})

	b.Run("fake", func(b *testing.B) {
		f := request.NewFetcher(fakeDoer(func(*http.Request) (*http.Response, error) {
			return okResponse(), nil
		}))

		b.ReportAllocs()
		b.ResetTimer()
		for i := 0; i < b.N; i++ {
			if _, err := f.Fetch(ctx, url); err != nil {
				b.Fatal(err)
			}
		}
	})

	// the fake wrapped in the metrics decorator: the difference from "fake" is
	// the cost of instrumentation
	b.Run("instrumented-fake", func(b *testing.B) {
		inner := fakeDoer(func(*http.Request) (*http.Response, error) {
			return okResponse(), nil
		})
		f := request.NewFetcher(request.NewInstrumentedDoer(inner, request.NewMetrics(prometheus.NewRegistry())))

		b.ReportAllocs()
		b.ResetTimer()
		for i := 0; i < b.N; i++ {
			if _, err := f.Fetch(ctx, url); err != nil {
				b.Fatal(err)
			}
		}
	})

	b.Run("httptest", func(b *testing.B) {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_, _ = io.WriteString(w, benchBody)
		}))
		defer srv.Close()
		f := request.NewFetcher(srv.Client())

		b.ReportAllocs()
		b.ResetTimer()
		for i := 0; i < b.N; i++ {
			if _, err := f.Fetch(ctx, srv.URL); err != nil {
				b.Fatal(err)
			}
		}
	})
}
