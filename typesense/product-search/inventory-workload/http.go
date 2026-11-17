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

type Client struct {
	HTTP     *http.Client
	Groups   [][]string
	Rotation []int
}

var errProtocol = errors.New("invalid Typesense response")

func newClient(mode string) (*Client, error) {
	groups := 1
	if mode == "sharded" {
		groups = 3
	} else if mode != "single" && mode != "cluster" {
		return nil, fmt.Errorf("invalid MODE %q", mode)
	}
	c := &Client{
		HTTP: &http.Client{
			Timeout:       15 * time.Second,
			Transport:     &http.Transport{MaxIdleConns: 32, MaxIdleConnsPerHost: 8, IdleConnTimeout: 90 * time.Second},
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
		},
		Groups: make([][]string, groups), Rotation: make([]int, groups),
	}
	for group := range c.Groups {
		replicas := 3
		if mode == "single" {
			replicas = 1
		}
		for replica := 1; replica <= replicas; replica++ {
			c.Groups[group] = append(c.Groups[group], fmt.Sprintf("http://ts%d:8108", group*3+replica))
		}
	}
	return c, nil
}

func (c *Client) request(ctx context.Context, endpoint, method, path string, data []byte) ([]byte, int, error) {
	req, err := http.NewRequestWithContext(ctx, method, endpoint+path, bytes.NewReader(data))
	if err != nil {
		return nil, 0, err
	}
	req.Header.Set("X-TYPESENSE-API-KEY", "local-search-key")
	req.Header.Set("Content-Type", "application/json")
	if strings.Contains(path, "/documents/import?") {
		req.Header.Set("Content-Type", "text/plain")
	}
	res, err := c.HTTP.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer res.Body.Close()
	body, readErr := io.ReadAll(io.LimitReader(res.Body, 4*1024*1024+1))
	if len(body) > 4*1024*1024 {
		return nil, res.StatusCode, fmt.Errorf("response exceeds workload limit")
	}
	if res.StatusCode != http.StatusOK {
		return body, res.StatusCode, fmt.Errorf("HTTP %d: %.500s", res.StatusCode, body)
	}
	return body, res.StatusCode, readErr
}

// Searches and document reads are safe to fail over. Mutations never use this path.
type ReadResult struct {
	Body     []byte
	Server   string
	Attempts int
	Failures []string
}

func (c *Client) read(ctx context.Context, group int, method, path string, body []byte) (ReadResult, error) {
	urls := c.Groups[group]
	first := c.Rotation[group] % len(urls)
	c.Rotation[group]++
	var failures []string
	for i := range urls {
		endpoint := urls[(first+i)%len(urls)]
		data, _, err := c.request(ctx, endpoint, method, path, body)
		if err == nil {
			return ReadResult{Body: data, Server: endpoint, Attempts: i + 1, Failures: failures}, nil
		}
		failures = append(failures, endpoint+": "+err.Error())
	}
	return ReadResult{Attempts: len(urls), Failures: failures}, fmt.Errorf("all replicas failed: %s", strings.Join(failures, "; "))
}

type ImportOutcome struct {
	ACK       []Product
	Rejected  []Product
	Ambiguous []Product
	Errors    []string
}

// Import responses are per-document and ordered; HTTP 200 does not make a batch atomic.
func parseImport(data []byte, rows []Product, complete bool) (ImportOutcome, error) {
	var outcome ImportOutcome
	lines := bytes.Split(bytes.TrimSpace(data), []byte{'\n'})
	if len(bytes.TrimSpace(data)) == 0 {
		lines = nil
	}
	for i, line := range lines {
		if i >= len(rows) {
			return outcome, fmt.Errorf("extra import outcome")
		}
		var value struct {
			Success *bool   `json:"success"`
			ID      *string `json:"id"`
			Error   string  `json:"error"`
		}
		err := json.Unmarshal(line, &value)
		if err != nil && !complete && i == len(lines)-1 {
			outcome.Ambiguous = append(outcome.Ambiguous, rows[i:]...)
			return outcome, nil
		}
		if err != nil || value.Success == nil || (value.ID != nil && *value.ID != rows[i].ID) || (*value.Success && value.ID == nil) {
			outcome.Ambiguous = append(outcome.Ambiguous, rows[i:]...)
			return outcome, fmt.Errorf("invalid import outcome for %s", rows[i].ID)
		}
		if *value.Success {
			outcome.ACK = append(outcome.ACK, rows[i])
		} else {
			outcome.Rejected = append(outcome.Rejected, rows[i])
			outcome.Errors = append(outcome.Errors, value.Error)
		}
	}
	if len(lines) < len(rows) {
		outcome.Ambiguous = append(outcome.Ambiguous, rows[len(lines):]...)
		if complete {
			return outcome, fmt.Errorf("short successful import response")
		}
	}
	return outcome, nil
}
