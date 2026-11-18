package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"path/filepath"
	"time"
)

type updateResult struct {
	Row      Product
	Endpoint string
	Response Response
	Err      error
	Seconds  float64
}

func updateInventory(ctx context.Context, client *Client, endpoint string, row Product) updateResult {
	body, err := json.Marshal(map[string]any{"fields": map[string]any{
		"stock":       map[string]int{"assign": row.Stock},
		"price_cents": map[string]int{"assign": row.PriceCents},
		"version":     map[string]int{"assign": row.Version},
	}})
	if err != nil {
		return updateResult{Row: row, Err: err}
	}
	parameters := url.Values{"timeout": {"3s"}, "create": {"false"}, "condition": {fmt.Sprintf("product.pid == %s and product.tenant == %d and product.version < %d", row.ID, row.Tenant, row.Version)}}
	started := time.Now()
	response, err := client.request(ctx, endpoint, http.MethodPut, "/document/v1/catalog/product/docid/"+row.ID+"?"+parameters.Encode(), body)
	return updateResult{Row: row, Endpoint: endpoint, Response: response, Err: err, Seconds: time.Since(started).Seconds()}
}

func writeInventory(ctx context.Context, root string, fixture *Fixture, client *Client, journal *Journal, startVersion, batch int) error {
	concurrency, err := boundedInteger("UPDATE_CONCURRENCY", 4, 1, 16)
	if err != nil {
		return err
	}
	version := startVersion
	requests, acknowledged, ambiguous, rejected := 0, 0, 0, 0
	for ctx.Err() == nil {
		stop, err := stopRequested(root, "writer")
		if err != nil {
			return err
		}
		if stop {
			break
		}
		if version >= 1000000000 {
			return fmt.Errorf("version safety limit exceeded")
		}
		rows := make([]Product, batch)
		for i := range rows {
			base := fixture.Products[((version-1)*batch+i)%len(fixture.Products)]
			row, err := fixture.product(base.ID, version)
			if err != nil {
				return err
			}
			rows[i] = row
		}
		if err := journal.append(Event{Stage: "attempt", Rows: rows}); err != nil {
			return err
		}
		tasks := make(chan int, len(rows))
		results := make(chan updateResult, len(rows))
		for i := range rows {
			tasks <- i
		}
		close(tasks)
		for range min(concurrency, len(rows)) {
			go func() {
				for index := range tasks {
					endpoint := client.URLs[((version-1)*batch+index)%len(client.URLs)]
					results <- updateInventory(ctx, client, endpoint, rows[index])
				}
			}()
		}
		var protocolErr error
		for range rows {
			result := <-results
			stage := "ambiguous"
			err := result.Err
			if err == nil {
				if validation := validateUpdate(result.Response, result.Row.ID); validation != nil {
					err = validation
					protocolErr = validation
				} else {
					stage = "ack"
				}
			} else if result.Response.Status == http.StatusPreconditionFailed {
				stage = "rejected"
			}
			event := Event{Stage: stage, Rows: []Product{result.Row}, Seconds: result.Seconds, Server: result.Endpoint, RequestID: fmt.Sprintf("%d/%s", version, result.Row.ID)}
			if err != nil {
				event.Error = fmt.Sprintf("%.500s", err.Error())
			}
			if err := journal.append(event); err != nil {
				return err
			}
			requests++
			switch stage {
			case "ack":
				acknowledged++
			case "rejected":
				rejected++
			default:
				ambiguous++
			}
		}
		if protocolErr != nil {
			return protocolErr
		}
		version++
		select {
		case <-ctx.Done():
		case <-time.After(100 * time.Millisecond):
		}
	}
	stopped, err := stopRequested(root, "writer")
	if err != nil {
		return err
	}
	return saveJSON(filepath.Join(root, "writer.json"), map[string]any{"stopped_by_controller": stopped, "requests": requests, "acknowledged_rows": acknowledged, "ambiguous_rows": ambiguous, "rejected_rows": rejected, "next_version": version, "concurrency": concurrency})
}
