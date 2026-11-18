package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"path/filepath"
	"strings"
	"time"
)

func search(ctx context.Context, client *Client, fixture *Fixture, index, tenant int, mode string, expected int) ([]Product, ReadResult, error) {
	nearest := "({targetHits:100,hnsw.exploreAdditionalHits:256}nearestNeighbor(embedding,q))"
	expression := "userInput(@text)"
	if mode == "dense" {
		expression = nearest
	} else if mode != "lexical" {
		expression = "(" + nearest + " OR " + expression + ")"
	}
	if tenant != 0 {
		expression += fmt.Sprintf(" AND tenant = %d", tenant)
	}
	query := map[string]any{"yql": "select pid,tenant,stock,price_cents,version from product where " + expression, "text": fixture.Queries[index].Text, "ranking": mode, "hits": 10, "timeout": "3s", "presentation.summary": "metadata"}
	if mode != "lexical" {
		query["input.query(q)"] = map[string]any{"values": fixture.Vectors[index]}
	}
	body, err := json.Marshal(query)
	if err != nil {
		return nil, ReadResult{}, err
	}
	response, err := client.read(ctx, http.MethodPost, "/search/", body)
	if err != nil {
		return nil, response, err
	}
	var result struct {
		Root struct {
			Errors   []json.RawMessage `json:"errors"`
			Coverage *struct {
				Percent float64 `json:"coverage"`
				Full    bool    `json:"full"`
			} `json:"coverage"`
			Children []struct {
				Fields Fields `json:"fields"`
			} `json:"children"`
		} `json:"root"`
	}
	if err := json.Unmarshal(response.Body, &result); err != nil {
		return nil, response, fmt.Errorf("%w: search JSON: %v", errProtocol, err)
	}
	if len(result.Root.Errors) > 0 {
		return nil, response, fmt.Errorf("search backend errors: %.500s", result.Root.Errors)
	}
	if result.Root.Coverage == nil || result.Root.Coverage.Percent < 100 || !result.Root.Coverage.Full {
		return nil, response, fmt.Errorf("incomplete search coverage")
	}
	if mode == "dense" && len(result.Root.Children) != min(10, expected) {
		return nil, response, fmt.Errorf("short dense result: %d/%d", len(result.Root.Children), min(10, expected))
	}
	products := make([]Product, 0, len(result.Root.Children))
	for _, hit := range result.Root.Children {
		product, err := hit.Fields.product()
		if err != nil {
			return nil, response, err
		}
		products = append(products, product)
	}
	return products, response, nil
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
	tenants := make(map[int]int)
	for _, product := range fixture.Products {
		tenants[product.Tenant]++
	}
	searches, reads, failed, stale, staleSearch, partial := 0, 0, 0, 0, 0, 0
	for index := 0; ctx.Err() == nil; index++ {
		stopped, err := stopRequested(root, "reader")
		if err != nil {
			return err
		}
		if stopped {
			break
		}
		if err := tail.consume(); err != nil {
			return err
		}
		mode := []string{"lexical", "dense", "hybrid", "rerank"}[index%4]
		tenant := 1 + index%100
		expected := tenants[tenant]
		if index%4 == 0 {
			tenant = 0
			mode = "dense"
			expected = len(fixture.Products)
		}
		started := time.Now()
		queryStart := float64(started.UnixNano()) / 1e9
		hits, response, searchErr := search(ctx, client, fixture, index%len(fixture.Queries), tenant, mode, expected)
		queryEnd := float64(time.Now().UnixNano()) / 1e9
		event := Event{Stage: "search", Mode: mode, Tenant: tenant, Seconds: time.Since(started).Seconds(), Hits: len(hits), Server: response.Server, Attempts: response.Attempts, Error: fmt.Sprintf("%.500s", strings.Join(response.Failures, "; "))}
		if searchErr != nil {
			if errors.Is(searchErr, errProtocol) {
				return searchErr
			}
			event.Stage = "search_error"
			event.Error = fmt.Sprintf("%.500s", searchErr.Error())
			failed++
		} else {
			if err := tail.consume(); err != nil {
				return err
			}
			seen := make(map[string]bool)
			for _, hit := range hits {
				if seen[hit.ID] || (tenant != 0 && hit.Tenant != tenant) {
					return fmt.Errorf("duplicate/tenant-leaked search hit: %s", hit.ID)
				}
				seen[hit.ID] = true
				mixed, err := tail.partialSearch(fixture, hit, queryStart, queryEnd)
				if err != nil {
					return err
				}
				if mixed {
					partial++
					if err := journal.append(Event{Stage: "partial_search_hit", Rows: []Product{hit}, Mode: mode, Tenant: tenant}); err != nil {
						return err
					}
				}
				minimum := tail.searchFloor(hit.ID, queryStart)
				if hit.Version < minimum {
					staleSearch++
					if err := journal.append(Event{Stage: "stale_search_hit", ID: hit.ID, Expected: minimum, Observed: hit.Version}); err != nil {
						return err
					}
				}
			}
			searches++
		}
		if err := journal.append(event); err != nil {
			return err
		}
		id := fixture.Products[(index*101)%len(fixture.Products)].ID
		if index%2 == 1 && len(tail.Recent) > 0 {
			id = tail.Recent[(index/2)%len(tail.Recent)]
		}
		minimum, ackTime := tail.ACK[id], tail.ACKTimes[id]
		started = time.Now()
		document, getErr := client.read(ctx, http.MethodGet, "/document/v1/catalog/product/docid/"+url.PathEscape(id), nil)
		event = Event{Stage: "get", ID: id, Expected: minimum, Seconds: time.Since(started).Seconds(), Server: document.Server, Attempts: document.Attempts, Error: fmt.Sprintf("%.500s", strings.Join(document.Failures, "; "))}
		if getErr != nil {
			event.Stage = "get_error"
			event.Error = fmt.Sprintf("%.500s", getErr.Error())
			failed++
		} else {
			if err := tail.consume(); err != nil {
				return err
			}
			var value struct {
				ID     string `json:"id"`
				Fields Fields `json:"fields"`
			}
			if err := json.Unmarshal(document.Body, &value); err != nil {
				return err
			}
			actual, err := value.Fields.product()
			if err != nil {
				return err
			}
			if value.ID != "id:catalog:product::"+id || actual.ID != id {
				return fmt.Errorf("document URI/payload identity differs")
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
	return saveJSON(filepath.Join(root, "reader.json"), map[string]any{"stopped_by_controller": stopped, "searches": searches, "reads": reads, "failed_operations": failed, "stale_reads": stale, "stale_search_hits": staleSearch, "partial_search_hits": partial})
}
