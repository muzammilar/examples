package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

var errProtocol = errors.New("invalid Vespa response")

type Client struct {
	HTTP     *http.Client
	URLs     []string
	Rotation int
}

type Response struct {
	Body   []byte
	Status int
	Header http.Header
}

type ReadResult struct {
	Response
	Server   string
	Attempts int
	Failures []string
}

func newClient(mode string) (*Client, error) {
	if mode != "single" && mode != "cluster" && mode != "sharded" {
		return nil, fmt.Errorf("invalid MODE %q", mode)
	}
	client := &Client{HTTP: &http.Client{
		Timeout:       10 * time.Second,
		Transport:     &http.Transport{MaxIdleConns: 32, MaxIdleConnsPerHost: 8, IdleConnTimeout: 90 * time.Second},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}}
	count := 3
	if mode == "single" {
		count = 1
	}
	for i := 0; i < count; i++ {
		client.URLs = append(client.URLs, fmt.Sprintf("http://node%d:8080", i))
	}
	return client, nil
}

func (c *Client) request(ctx context.Context, endpoint, method, path string, data []byte) (Response, error) {
	request, err := http.NewRequestWithContext(ctx, method, endpoint+path, bytes.NewReader(data))
	if err != nil {
		return Response{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := c.HTTP.Do(request)
	if err != nil {
		return Response{}, err
	}
	defer response.Body.Close()
	body, readErr := io.ReadAll(io.LimitReader(response.Body, 4*1024*1024+1))
	result := Response{Body: body, Status: response.StatusCode, Header: response.Header}
	if len(body) > 4*1024*1024 {
		return result, fmt.Errorf("response exceeds workload limit")
	}
	if response.StatusCode != http.StatusOK {
		return result, fmt.Errorf("HTTP %d: %.500s", response.StatusCode, body)
	}
	return result, readErr
}

// Only document GETs and read-only searches use endpoint failover.
func (c *Client) read(ctx context.Context, method, path string, data []byte) (ReadResult, error) {
	first := c.Rotation % len(c.URLs)
	c.Rotation++
	var failures []string
	for i := range c.URLs {
		endpoint := c.URLs[(first+i)%len(c.URLs)]
		response, err := c.request(ctx, endpoint, method, path, data)
		if err == nil {
			return ReadResult{Response: response, Server: endpoint, Attempts: i + 1, Failures: failures}, nil
		}
		failures = append(failures, endpoint+": "+err.Error())
	}
	return ReadResult{Attempts: len(c.URLs), Failures: failures}, fmt.Errorf("all endpoints failed: %s", strings.Join(failures, "; "))
}

type Fields struct {
	PID        *int `json:"pid"`
	Tenant     int  `json:"tenant"`
	Stock      int  `json:"stock"`
	PriceCents int  `json:"price_cents"`
	Version    int  `json:"version"`
}

func (f Fields) product() (Product, error) {
	if f.PID == nil || *f.PID < 0 {
		return Product{}, fmt.Errorf("%w: missing/invalid pid", errProtocol)
	}
	return Product{ID: fmt.Sprint(*f.PID), Tenant: f.Tenant, Stock: f.Stock, PriceCents: f.PriceCents, Version: f.Version}, nil
}

func validateUpdate(response Response, id string) error {
	if response.Header.Get("X-Vespa-Ignored-Operation") == "true" || response.Header.Get("X-Vespa-Ignored-Fields") == "true" {
		return fmt.Errorf("%w: update or fields were ignored", errProtocol)
	}
	var value struct {
		ID   string `json:"id"`
		Path string `json:"pathId"`
	}
	if err := json.Unmarshal(response.Body, &value); err != nil {
		return fmt.Errorf("%w: update JSON: %v", errProtocol, err)
	}
	if value.ID != "id:catalog:product::"+id || value.Path != "/document/v1/catalog/product/docid/"+id {
		return fmt.Errorf("%w: update response identity differs", errProtocol)
	}
	return nil
}
