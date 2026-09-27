// Package request fetches web pages over HTTP.
//
// The Fetcher does not depend on *http.Client directly. Instead it depends on the
// small Doer interface, which *http.Client already satisfies. This lets tests
// replace the network with either a generated gomock mock (see mocks/) or an
// in-process httptest server, without changing any production code.
package request

//go:generate go run go.uber.org/mock/mockgen@v0.6.0 -source=request.go -destination=mocks/mock_doer.go -package=mocks

import (
	"context"
	"fmt"
	"io"
	"net/http"
)

// Doer is the subset of *http.Client used by Fetcher.
type Doer interface {
	Do(req *http.Request) (*http.Response, error)
}

// Page is a summary of a fetched URL.
type Page struct {
	URL        string
	StatusCode int
	Bytes      int
}

// Fetcher performs GET requests using the provided Doer.
type Fetcher struct {
	client Doer
}

// NewFetcher returns a Fetcher. If client is nil, http.DefaultClient is used.
func NewFetcher(client Doer) *Fetcher {
	if client == nil {
		client = http.DefaultClient
	}
	return &Fetcher{client: client}
}

// Fetch issues a GET to url and returns a summary of the response. Non-2xx
// responses are returned as errors.
func (f *Fetcher) Fetch(ctx context.Context, url string) (*Page, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, fmt.Errorf("building request: %w", err)
	}
	req.Header.Set("User-Agent", "mock-request-example")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("performing request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("reading body: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode > 299 {
		return nil, fmt.Errorf("unexpected status %d from %s", resp.StatusCode, url)
	}
	return &Page{URL: url, StatusCode: resp.StatusCode, Bytes: len(body)}, nil
}
