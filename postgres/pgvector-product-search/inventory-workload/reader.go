package main

import (
	"context"
	"fmt"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

func vectorLiteral(vector []float32) string {
	values := make([]string, len(vector))
	for i, v := range vector {
		values[i] = strconv.FormatFloat(float64(v), 'g', -1, 32)
	}
	return "[" + strings.Join(values, ",") + "]"
}
func readInventory(ctx context.Context, root string, f *Fixture, d *database, j *Journal) (runErr error) {
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
	reads, searches, failures := 0, 0, 0
	for i := 0; ctx.Err() == nil; i++ {
		stop, err := stopRequested(root, "reader")
		if err != nil {
			return err
		}
		if stop {
			break
		}
		if err = h.consume(root); err != nil {
			return err
		}
		id := f.Products[(i*101)%len(f.Products)].ID
		if i%2 == 1 && h.recent != "" {
			id = h.recent
		}
		numericID, err := strconv.ParseInt(id, 10, 64)
		if err != nil {
			return err
		}
		floor := h.acks[id]
		for _, kind := range []string{"get", "search"} {
			started := time.Now()
			event := Event{Stage: kind, ID: id, Expected: floor}
			op, cancel := context.WithTimeout(ctx, 3*time.Second)
			pool, host, opErr := d.primary(op)
			event.Server = host
			var products []Product
			floors := make(map[string]int)
			if kind == "search" {
				for key, v := range h.acks {
					floors[key] = v
				}
			}
			if opErr == nil {
				if kind == "get" {
					var p Product
					opErr = pool.QueryRow(op, "SELECT id::text,tenant,stock,price_cents,version FROM inventory WHERE id=$1::bigint", numericID).Scan(&p.ID, &p.Tenant, &p.Stock, &p.PriceCents, &p.Version)
					if opErr == nil {
						products = append(products, p)
					}
				} else {
					tenant := 1 + i%100
					event.Tenant = tenant
					event.Mode = "dense"
					rows, queryErr := pool.Query(op, "SELECT i.id::text,i.tenant,i.stock,i.price_cents,i.version FROM products p JOIN inventory i USING(id) WHERE i.tenant=$1 ORDER BY p.embedding <=> $2::vector LIMIT 10", tenant, vectorLiteral(f.Vectors[i%len(f.Vectors)]))
					opErr = queryErr
					if opErr == nil {
						for rows.Next() {
							var p Product
							if opErr = rows.Scan(&p.ID, &p.Tenant, &p.Stock, &p.PriceCents, &p.Version); opErr != nil {
								break
							}
							products = append(products, p)
						}
						if opErr == nil {
							opErr = rows.Err()
						}
						rows.Close()
					}
				}
			}
			event.Seconds = time.Since(started).Seconds()
			cancel()
			if opErr != nil {
				event.Stage += "_error"
				event.Error = fmt.Sprintf("%.500s", opErr)
				failures++
			} else {
				if err = h.consume(root); err != nil {
					return err
				}
				seen := make(map[string]bool)
				for _, p := range products {
					if seen[p.ID] || (kind == "search" && p.Tenant != event.Tenant) {
						return fmt.Errorf("duplicate/tenant leak")
					}
					seen[p.ID] = true
					minimum := floor
					if kind == "search" {
						minimum = floors[p.ID]
					}
					if err = h.check(f, p, minimum); err != nil {
						return err
					}
					if kind == "get" {
						event.Observed = p.Version
					}
				}
				if kind == "search" {
					if len(products) != 10 {
						return fmt.Errorf("short tenant search: %d", len(products))
					}
					searches++
				} else {
					reads++
				}
				event.Hits = len(products)
			}
			if err = j.append(event); err != nil {
				return err
			}
		}
		select {
		case <-ctx.Done():
		case <-time.After(50 * time.Millisecond):
		}
	}
	stop, err := stopRequested(root, "reader")
	if err != nil {
		return err
	}
	if !stop {
		return fmt.Errorf("reader exhausted duration")
	}
	return saveJSON(filepath.Join(root, "reader.json"), map[string]any{"stopped_by_controller": stop, "reads": reads, "searches": searches, "failed_operations": failures, "wrong_reads": 0})
}
