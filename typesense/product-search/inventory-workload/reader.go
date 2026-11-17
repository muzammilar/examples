package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type Hit struct {
	Document Product  `json:"document"`
	Distance *float64 `json:"vector_distance"`
}

func search(ctx context.Context, c *Client, f *Fixture, index, tenant int, mode string) ([]Hit, int, []string, error) {
	groups := []int{0}
	if len(c.Groups) == 3 {
		if tenant == 0 {
			groups = []int{0, 1, 2}
		} else {
			groups = []int{tenant % 3}
		}
	}
	vector, err := json.Marshal(f.Vectors[index])
	if err != nil {
		return nil, 0, nil, err
	}
	query := map[string]any{"collection": "products", "q": f.Queries[index].Text, "query_by": "name,description,brand", "per_page": 10, "exclude_fields": "embedding,description"}
	if mode == "dense" {
		query["q"] = "*"
	}
	if mode != "lexical" {
		query["vector_query"] = "embedding:(" + string(vector) + ", k:100, ef:256, alpha:0.5)"
	}
	if tenant != 0 {
		query["filter_by"] = fmt.Sprintf("tenant:=%d", tenant)
	}
	body, err := json.Marshal(map[string]any{"searches": []any{query}})
	if err != nil {
		return nil, 0, nil, err
	}
	type part struct {
		result ReadResult
		err    error
	}
	parts := make(chan part, len(groups))
	for _, group := range groups {
		go func(group int) {
			result, err := c.read(ctx, group, http.MethodPost, "/multi_search", body)
			parts <- part{result, err}
		}(group)
	}
	var hits []Hit
	var failures []string
	attempts := 0
	var firstErr error
	for range groups {
		part := <-parts
		attempts += part.result.Attempts
		failures = append(failures, part.result.Failures...)
		if part.err != nil {
			if firstErr == nil {
				firstErr = part.err
			}
			continue
		}
		var value struct {
			Results []struct {
				Hits   []Hit  `json:"hits"`
				Error  string `json:"error"`
				Code   int    `json:"code"`
				Found  *int   `json:"found"`
				Cutoff *bool  `json:"search_cutoff"`
			} `json:"results"`
		}
		if err := json.Unmarshal(part.result.Body, &value); err != nil {
			return nil, attempts, failures, fmt.Errorf("%w: search JSON: %v", errProtocol, err)
		}
		if len(value.Results) != 1 {
			return nil, attempts, failures, fmt.Errorf("%w: search result: %.500s", errProtocol, part.result.Body)
		}
		if value.Results[0].Error != "" {
			if value.Results[0].Code >= 500 {
				firstErr = fmt.Errorf("search HTTP-equivalent %d: %s", value.Results[0].Code, value.Results[0].Error)
				continue
			}
			return nil, attempts, failures, fmt.Errorf("%w: search result: %.500s", errProtocol, part.result.Body)
		}
		if mode == "dense" && len(value.Results[0].Hits) < 10 {
			found := "unreported"
			if value.Results[0].Found != nil {
				found = fmt.Sprint(*value.Results[0].Found)
			}
			return nil, attempts, failures, fmt.Errorf("%w: short dense search query=%d tenant=%d server=%s hits=%d found=%s", errProtocol, index, tenant, part.result.Server, len(value.Results[0].Hits), found)
		}
		hits = append(hits, value.Results[0].Hits...)
	}
	if firstErr != nil {
		return nil, attempts, failures, firstErr
	}
	if tenant == 0 && len(groups) > 1 {
		for _, hit := range hits {
			if hit.Distance == nil {
				return nil, attempts, failures, fmt.Errorf("%w: global dense result missing distance", errProtocol)
			}
		}
		sort.Slice(hits, func(i, j int) bool {
			if *hits[i].Distance == *hits[j].Distance {
				return hits[i].Document.ID < hits[j].Document.ID
			}
			return *hits[i].Distance < *hits[j].Distance
		})
		if len(hits) > 10 {
			hits = hits[:10]
		}
	}
	return hits, attempts, failures, nil
}

func readInventory(ctx context.Context, root string, fixture *Fixture, client *Client, journal *Journal) (runErr error) {
	defer func() {
		if runErr != nil {
			runErr = errors.Join(runErr, journal.append(Event{Stage: "wrong", Error: fmt.Sprintf("%.500s", runErr.Error())}))
		}
	}()
	tail, err := loadHistory(root)
	if err != nil {
		return err
	}
	defer func() {
		if tail.file != nil {
			tail.file.Close()
		}
	}()
	searches, reads, stale, failures := 0, 0, 0, 0
	for i := 0; ctx.Err() == nil; i++ {
		stop, err := stopRequested(root, "reader")
		if err != nil {
			return err
		}
		if stop {
			break
		}
		if err := tail.consume(); err != nil {
			return err
		}
		mode := []string{"lexical", "dense", "hybrid"}[i%3]
		tenant := 1 + i%100
		if i%4 == 0 {
			tenant = 0
			mode = "dense"
		}
		started := time.Now()
		hits, attempts, failed, searchErr := search(ctx, client, fixture, i%len(fixture.Queries), tenant, mode)
		event := Event{Stage: "search", Mode: mode, Tenant: tenant, Hits: len(hits), Seconds: time.Since(started).Seconds(), Attempts: attempts, Error: fmt.Sprintf("%.500s", strings.Join(failed, "; "))}
		if searchErr != nil {
			if errors.Is(searchErr, errProtocol) {
				return searchErr
			}
			event.Stage = "search_error"
			event.Error = fmt.Sprintf("%.500s", searchErr.Error())
			failures++
		} else {
			if err := tail.consume(); err != nil {
				return err
			}
			seen := make(map[string]bool)
			for _, hit := range hits {
				if seen[hit.Document.ID] || (tenant != 0 && hit.Document.Tenant != tenant) {
					return fmt.Errorf("duplicate or leaked search result %s", hit.Document.ID)
				}
				seen[hit.Document.ID] = true
				if err := tail.checked(fixture, hit.Document); err != nil {
					return err
				}
			}
			if mode == "dense" && len(hits) != 10 {
				return fmt.Errorf("dense search returned %d/10 results", len(hits))
			}
			searches++
		}
		if err := journal.append(event); err != nil {
			return err
		}
		id := fixture.Products[(i*101)%len(fixture.Products)].ID
		if i%2 == 1 && len(tail.Recent) > 0 {
			id = tail.Recent[(i/2)%len(tail.Recent)]
		}
		minimum := tail.ACK[id]
		ackTime := tail.ACKTimes[id]
		baseline, err := fixture.product(id, 0)
		if err != nil {
			return err
		}
		group := 0
		if len(client.Groups) == 3 {
			group = baseline.Tenant % 3
		}
		started = time.Now()
		response, readErr := client.read(ctx, group, http.MethodGet, "/collections/products/documents/"+url.PathEscape(id), nil)
		event = Event{Stage: "get", ID: id, Expected: minimum, Seconds: time.Since(started).Seconds(), Server: response.Server, Attempts: response.Attempts, Error: fmt.Sprintf("%.500s", strings.Join(response.Failures, "; "))}
		if readErr != nil {
			event.Stage = "get_error"
			event.Error = fmt.Sprintf("%.500s", readErr.Error())
			failures++
		} else {
			if err := tail.consume(); err != nil {
				return err
			}
			var actual Product
			if err := json.Unmarshal(response.Body, &actual); err != nil {
				return err
			}
			if actual.ID != id {
				return fmt.Errorf("document GET returned wrong ID")
			}
			if err := tail.checked(fixture, actual); err != nil {
				return err
			}
			event.Observed = actual.Version
			if minimum > 0 {
				event.ACKAge = float64(time.Now().UnixNano())/1e9 - ackTime
			}
			if actual.Version < minimum {
				event.Stage = "stale"
				stale++
			} else {
				reads++
			}
		}
		if err := journal.append(event); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
		case <-time.After(50 * time.Millisecond):
		}
	}
	stopped, err := stopRequested(root, "reader")
	if err != nil {
		return err
	}
	return saveJSON(filepath.Join(root, "reader.json"), map[string]any{"stopped_by_controller": stopped, "searches": searches, "reads": reads, "stale_reads": stale, "failed_operations": failures})
}
