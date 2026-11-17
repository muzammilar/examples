package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"path/filepath"
	"strings"
	"time"
)

func writeInventory(ctx context.Context, root string, fixture *Fixture, client *Client, journal *Journal, startVersion, batch int) error {
	version := startVersion
	requests, acknowledgements, ambiguous, rejected := 0, 0, 0, 0
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
		groups := make([][]Product, len(client.Groups))
		for i := range rows {
			base := fixture.Products[((version-1)*batch+i)%len(fixture.Products)]
			row, err := fixture.product(base.ID, version)
			if err != nil {
				return err
			}
			rows[i] = row
			group := 0
			if len(groups) == 3 {
				group = row.Tenant % 3
			}
			groups[group] = append(groups[group], row)
		}
		if err := journal.append(Event{Stage: "attempt", Rows: rows}); err != nil {
			return err
		}
		for group, updates := range groups {
			if len(updates) == 0 {
				continue
			}
			var body bytes.Buffer
			for _, row := range updates {
				if err := json.NewEncoder(&body).Encode(row); err != nil {
					return err
				}
			}
			urls := client.Groups[group]
			endpoint := urls[client.Rotation[group]%len(urls)]
			client.Rotation[group]++
			started := time.Now()
			data, status, requestErr := client.request(ctx, endpoint, http.MethodPost,
				"/collections/products/documents/import?action=update&return_id=true", body.Bytes())
			elapsed := time.Since(started).Seconds()
			requests++
			outcome := ImportOutcome{Ambiguous: updates}
			var protocolErr error
			if status == http.StatusOK {
				outcome, protocolErr = parseImport(data, updates, requestErr == nil)
			}
			for _, result := range []struct {
				stage string
				rows  []Product
			}{{"ack", outcome.ACK}, {"rejected", outcome.Rejected}, {"ambiguous", outcome.Ambiguous}} {
				if len(result.rows) == 0 {
					continue
				}
				event := Event{Stage: result.stage, Rows: result.rows, Seconds: elapsed, Server: endpoint}
				event.RequestID = fmt.Sprintf("%d/%d", version, group)
				if result.stage == "rejected" {
					event.Error = fmt.Sprintf("%.500s", strings.Join(outcome.Errors, "; "))
				}
				if requestErr != nil {
					event.Error = fmt.Sprintf("%.500s", requestErr.Error())
				}
				if protocolErr != nil {
					event.Error = protocolErr.Error()
				}
				if err := journal.append(event); err != nil {
					return err
				}
			}
			acknowledgements += len(outcome.ACK)
			ambiguous += len(outcome.Ambiguous)
			rejected += len(outcome.Rejected)
			if protocolErr != nil {
				return protocolErr
			}
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
	return saveJSON(filepath.Join(root, "writer.json"), map[string]any{
		"stopped_by_controller": stopped, "requests": requests, "acknowledged_rows": acknowledgements,
		"ambiguous_rows": ambiguous, "rejected_rows": rejected, "next_version": version,
	})
}
