package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
)

// Only this example's local API is reachable; never discover endpoints or credentials.
func openObserveJSON(ctx context.Context, method, path string, payload any) (json.RawMessage, error) {
	var body io.Reader
	if payload != nil {
		raw, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(raw)
	}
	request, err := http.NewRequestWithContext(ctx, method, "http://openobserve:5080"+path, body)
	if err != nil {
		return nil, err
	}
	request.SetBasicAuth("incident@example.test", "Local-Incident-P4ssword!")
	request.Header.Set("Content-Type", "application/json")
	response, err := httpClient.Do(request)
	if err != nil {
		return nil, err
	}
	raw, readErr := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
	closeErr := response.Body.Close()
	if err = errors.Join(readErr, closeErr); err != nil {
		return nil, err
	}
	if len(raw) > 16<<20 {
		return nil, errors.New("native response exceeded 16 MiB")
	}
	if response.StatusCode/100 != 2 {
		return nil, fmt.Errorf("OpenObserve HTTP %d: %s", response.StatusCode, raw)
	}
	if !json.Valid(raw) {
		return nil, errors.New("native response is not JSON")
	}
	return raw, nil
}

func inspectOpenObserve(ctx context.Context) error {
	for _, kind := range []string{"logs", "metrics", "traces"} {
		path := "/api/default/streams?fetchSchema=true&type=" + kind
		raw, err := openObserveJSON(ctx, "GET", path, nil)
		if err != nil {
			return err
		}
		if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"kind": kind, "response": raw, "meaning": "Native schema inspection only; not correctness qualification"}); err != nil {
			return err
		}
	}
	return nil
}

func sampleOpenObserve(ctx context.Context, f Fixture) error {
	for _, kind := range []string{"logs", "metrics", "traces"} {
		stream := "default"
		if kind == "metrics" {
			stream = "checkout_latency_microseconds"
		}
		payload := map[string]any{"query": map[string]any{
			"sql":        fmt.Sprintf(`SELECT * FROM "%s" WHERE run='%s' LIMIT 1`, stream, f.Run),
			"start_time": f.StartMS*1000 - 1, "end_time": (f.StartMS + int64(f.Requests)*10 + 100000) * 1000,
			"from": 0, "size": 1,
		}}
		raw, err := openObserveJSON(ctx, "POST", "/api/default/_search?type="+kind, payload)
		if err != nil {
			return err
		}
		if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"kind": kind, "response": raw, "meaning": "Bounded native sample; not a full correctness result"}); err != nil {
			return err
		}
	}
	return nil
}
