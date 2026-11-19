package main

import (
	"context"
	"errors"
	"fmt"
	"path/filepath"
	"time"
)

func readInventory(ctx context.Context, root string, f *Fixture, c *Client, j *Journal) (runErr error) {
	defer func() {
		if runErr != nil {
			_ = j.append(Event{Stage: "wrong", Error: runErr.Error()})
		}
	}()
	h, err := newHistory(root)
	if err != nil {
		return err
	}
	defer func() {
		if h.file != nil {
			h.file.Close()
		}
	}()
	tenants := make(map[int]int)
	for _, p := range f.Products {
		tenants[p.Tenant]++
	}
	reads, searches, failed, additional := 0, 0, 0, 0
	for index := 0; ctx.Err() == nil; index++ {
		stopped, err := stopRequested(root, "reader")
		if err != nil {
			return err
		}
		if stopped {
			break
		}
		for kind, stage := range []string{"get", "search"} {
			if err = h.consume(root); err != nil {
				return err
			}
			id := f.Products[(index*101)%len(f.Products)].ID
			if index%2 == 1 && h.recent != "" {
				id = h.recent
			}
			floor := h.acks[id]
			floors := make(map[string]int)
			if kind == 1 {
				for key, value := range h.acks {
					floors[key] = value
				}
			}
			tenant := 1 + index%100
			endpoint := c.readers[(index*2+kind)%len(c.readers)]
			event := Event{Stage: stage, ID: id, Expected: floor, Server: endpoint}
			started := time.Now()
			op, cancel := context.WithTimeout(ctx, 3*time.Second)
			var rows []Product
			var reply Response
			var catalog int
			var requestErr error
			if kind == 0 {
				var p Product
				p, catalog, reply, requestErr = c.get(op, endpoint, id)
				if requestErr == nil {
					rows = []Product{p}
				}
			} else {
				rows, catalog, reply, requestErr = c.search(op, endpoint, f, index%len(f.Queries), tenant)
				event.Tenant = tenant
				event.Mode = "dense"
			}
			cancel()
			event.Seconds = time.Since(started).Seconds()
			event.Attempts = reply.Attempts
			event.CatalogVersion = catalog
			additional += max(0, reply.Attempts-1)
			if requestErr != nil {
				event.Stage += "_error"
				event.Error = fmt.Sprintf("%.500s", requestErr)
				failed++
			} else {
				if err = h.consume(root); err != nil {
					return err
				}
				seen := make(map[string]bool)
				for _, p := range rows {
					if seen[p.ID] || (kind == 1 && p.Tenant != tenant) {
						return fmt.Errorf("duplicate/tenant-leaked result")
					}
					seen[p.ID] = true
					minimum := floor
					if kind == 1 {
						minimum = floors[p.ID]
					}
					if kind == 0 && p.ID != id {
						return fmt.Errorf("point read returned another product")
					}
					if err = h.check(f, p, minimum); err != nil {
						return err
					}
					if kind == 0 {
						event.Observed = p.Version
					}
				}
				if kind == 1 {
					if len(rows) != min(10, tenants[tenant]) {
						return fmt.Errorf("short dense tenant result")
					}
					searches++
				} else {
					reads++
				}
				event.Hits = len(rows)
			}
			if err = j.append(event); err != nil {
				return err
			}
			if errors.Is(requestErr, errProtocol) {
				return requestErr
			}
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
	if !stopped {
		return fmt.Errorf("reader exhausted duration")
	}
	return saveJSON(filepath.Join(root, "reader.json"), map[string]any{"stopped_by_controller": true, "reads": reads, "searches": searches, "failed_operations": failed, "wrong_reads": 0, "additional_http_read_attempts": additional})
}
