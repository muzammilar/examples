package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
)

var errProtocol = errors.New("invalid wrapper reply")

type Client struct {
	http    *http.Client
	readers []string
	writer  string
}
type Response struct {
	Body     []byte
	Status   int
	Attempts int
	Server   string
}

func newClient() (*Client, error) {
	readers := strings.Split(os.Getenv("URLS"), ",")
	for _, value := range readers {
		parsed, err := url.Parse(value)
		if err != nil || parsed.Scheme != "http" || parsed.Host == "" || parsed.Path != "" {
			return nil, fmt.Errorf("invalid read endpoint %q", value)
		}
	}
	return &Client{http: &http.Client{}, readers: readers, writer: "http://writer:8080"}, nil
}
func (c *Client) request(ctx context.Context, server, method, path string, body any) (Response, error) {
	var encoded []byte
	if body != nil {
		var err error
		encoded, err = json.Marshal(body)
		if err != nil {
			return Response{}, err
		}
	}
	req, err := http.NewRequestWithContext(ctx, method, server+path, bytes.NewReader(encoded))
	if err != nil {
		return Response{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	// The standard transport can retry safe reads on a stale connection. Count actual writes of the request.
	var attempts atomic.Int32
	req = req.WithContext(httptrace.WithClientTrace(req.Context(), &httptrace.ClientTrace{WroteRequest: func(httptrace.WroteRequestInfo) { attempts.Add(1) }}))
	reply, err := c.http.Do(req)
	result := Response{Server: server, Attempts: int(attempts.Load())}
	if err != nil {
		return result, err
	}
	defer reply.Body.Close()
	result.Status = reply.StatusCode
	result.Body, err = io.ReadAll(io.LimitReader(reply.Body, 2*1024*1024+1))
	if err != nil {
		return result, err
	}
	if len(result.Body) > 2*1024*1024 {
		return result, fmt.Errorf("%w: oversized body", errProtocol)
	}
	if result.Status != 200 {
		return result, fmt.Errorf("HTTP %d: %.500s", result.Status, result.Body)
	}
	return result, nil
}
func product(data json.RawMessage) (Product, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return Product{}, fmt.Errorf("%w: %v", errProtocol, err)
	}
	for _, key := range []string{"id", "tenant", "stock", "price_cents", "version"} {
		value, ok := fields[key]
		if !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return Product{}, fmt.Errorf("%w: missing/null %s", errProtocol, key)
		}
	}
	var p Product
	if err := json.Unmarshal(data, &p); err != nil {
		return p, fmt.Errorf("%w: %v", errProtocol, err)
	}
	return p, nil
}
func (c *Client) get(ctx context.Context, endpoint, id string) (Product, int, Response, error) {
	reply, err := c.request(ctx, endpoint, "GET", "/inventory/"+url.PathEscape(id), nil)
	if err != nil {
		if reply.Status == 404 {
			err = fmt.Errorf("%w: known product missing", errProtocol)
		}
		return Product{}, 0, reply, err
	}
	var envelope struct {
		Row     json.RawMessage `json:"row"`
		Version int             `json:"catalog_version"`
	}
	if err = json.Unmarshal(reply.Body, &envelope); err != nil || envelope.Version < 1 {
		return Product{}, 0, reply, fmt.Errorf("%w: invalid point envelope", errProtocol)
	}
	p, err := product(envelope.Row)
	return p, envelope.Version, reply, err
}
func (c *Client) search(ctx context.Context, endpoint string, f *Fixture, index, tenant int) ([]Product, int, Response, error) {
	reply, err := c.request(ctx, endpoint, "POST", "/search", map[string]any{"text": f.Queries[index].Text, "vector": f.Vectors[index], "mode": "dense", "tenant": tenant, "k": 10})
	if err != nil {
		return nil, 0, reply, err
	}
	var envelope struct {
		Hits    []json.RawMessage `json:"hits"`
		Version int               `json:"catalog_version"`
	}
	if err = json.Unmarshal(reply.Body, &envelope); err != nil || envelope.Version < 1 {
		return nil, 0, reply, fmt.Errorf("%w: invalid search envelope", errProtocol)
	}
	rows := make([]Product, len(envelope.Hits))
	for i, value := range envelope.Hits {
		rows[i], err = product(value)
		if err != nil {
			return nil, 0, reply, err
		}
	}
	return rows, envelope.Version, reply, nil
}
func (c *Client) mutate(ctx context.Context, rows []Product) (version, nativeAttempts int, reply Response, err error) {
	reply, err = c.request(ctx, c.writer, "POST", "/update", map[string]any{"rows": rows})
	if err != nil {
		return
	}
	var result struct {
		Updated  int `json:"updated"`
		Version  int `json:"catalog_version"`
		Attempts int `json:"native_attempts"`
	}
	if decode := json.Unmarshal(reply.Body, &result); decode != nil || result.Updated != len(rows) || result.Version < 1 || result.Attempts < 1 {
		return 0, 0, reply, fmt.Errorf("%w: invalid mutation acknowledgement", errProtocol)
	}
	return result.Version, result.Attempts, reply, nil
}
func requestID(version int) string { return strconv.Itoa(version) }
