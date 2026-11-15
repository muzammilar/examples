package main

import (
	"container/heap"
	"fmt"
	"math"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

type candidate struct {
	ID    uint64  `json:"id"`
	Score float64 `json:"score"`
}
type topHeap []candidate

func (h topHeap) Len() int { return len(h) }
func (h topHeap) Less(i, j int) bool {
	return h[i].Score < h[j].Score || (h[i].Score == h[j].Score && h[i].ID > h[j].ID)
}
func (h topHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }
func (h *topHeap) Push(v any)   { *h = append(*h, v.(candidate)) }
func (h *topHeap) Pop() any     { old := *h; v := old[len(old)-1]; *h = old[:len(old)-1]; return v }
func exact(vector []float32, tenant *int) []candidate {
	best := &topHeap{}
	heap.Init(best)
	for id, stored := range baseCache {
		if tenant != nil && id%100 != *tenant {
			continue
		}
		score := 0.0
		for d, value := range stored {
			score += float64(value) * float64(vector[d])
		}
		point := candidate{uint64(id), score}
		if best.Len() < 10 {
			heap.Push(best, point)
		} else if score > (*best)[0].Score {
			(*best)[0] = point
			heap.Fix(best, 0)
		}
	}
	out := []candidate(*best)
	sort.Slice(out, func(i, j int) bool { return out[i].Score > out[j].Score })
	return out
}

type hit struct {
	ID      uint64  `json:"id"`
	Score   float64 `json:"score"`
	Payload payload `json:"payload"`
}

func queryBody(vector []float32, tenant *int, exactSearch bool, quantized, rescore bool, oversampling float64) map[string]any {
	conditions := []any{map[string]any{"key": "kind", "match": map[string]any{"value": "base"}}}
	if tenant != nil {
		conditions = append(conditions, map[string]any{"key": "tenant", "match": map[string]any{"value": *tenant}})
	}
	request := map[string]any{"query": vector, "limit": 10, "with_payload": true, "filter": map[string]any{"must": conditions}, "params": map[string]any{"exact": exactSearch, "hnsw_ef": number("HNSW_EF", 128, 16, 2048), "quantization": map[string]any{"ignore": !quantized, "rescore": rescore, "oversampling": oversampling}}}
	if tenant != nil {
		request["shard_key"] = fmt.Sprintf("customers-%d", *tenant%3)
	}
	return request
}
func search(host, mode string, request any) ([]hit, error) {
	var result struct {
		Points []hit `json:"points"`
	}
	err := call(host, "POST", "/collections/"+mode+"/points/query?timeout=60", request, &result)
	return result.Points, err
}
func validateHits(points []hit, tenant *int) error {
	if len(points) != 10 {
		return fmt.Errorf("ANN returned %d hits; expected k=10", len(points))
	}
	seen := map[uint64]bool{}
	for _, p := range points {
		if seen[p.ID] || p.ID >= uint64(count()) || p.Payload.Kind != "base" || math.IsNaN(p.Score) || math.IsInf(p.Score, 0) {
			return fmt.Errorf("invalid ANN hit %d", p.ID)
		}
		if p.Payload.Tenant != int(p.ID%100) || p.Payload.Digest != baseDigests[p.ID] || (tenant != nil && p.Payload.Tenant != *tenant) {
			return fmt.Errorf("ANN payload or tenant isolation violation %d", p.ID)
		}
		seen[p.ID] = true
	}
	return nil
}

func benchmark() {
	cacheBase()
	queries := number("QUERIES", 100, 20, 1000)
	duration := time.Duration(number("DURATION", 10, 1, 120)) * time.Second
	concurrency := number("CONCURRENCY", 4, 1, 32)
	type task struct {
		Vector []float32
		Tenant *int
		Truth  []candidate
	}
	datasets := map[string][]task{}
	for _, filter := range []string{"all-base", "one-customer"} {
		for i := 0; i < queries; i++ {
			id := uint64(count() + 1000000 + i)
			vector := embedding(id)
			var tenant *int
			if filter == "one-customer" {
				value := int(id % 100)
				tenant = &value
			}
			datasets[filter] = append(datasets[filter], task{vector, tenant, exact(vector, tenant)})
		}
	}
	out := map[string]any{}
	for _, mode := range modes {
		variants := []struct {
			Name               string
			Quantized, Rescore bool
			Oversampling       float64
		}{{"hnsw", false, false, 1}}
		if mode == "scalar8" || mode == "turbo4" {
			variants = []struct {
				Name               string
				Quantized, Rescore bool
				Oversampling       float64
			}{{"compressed", true, false, 1}, {"rescore-2x", true, true, 2}, {"rescore-4x", true, true, 4}}
		}
		for _, filter := range []string{"all-base", "one-customer"} {
			tasks := datasets[filter]
			// Validate exact ground truth separately from every timed search variant.
			// Interleaving exact scans only before some variants changes their cache
			// and transport conditions, making the latency comparison unfair.
			for i, task := range tasks {
				host := hosts()[i%len(hosts())]

				native, err := search(host, mode, queryBody(task.Vector, task.Tenant, true, false, false, 1))
				check(err)
				if len(native) != 10 {
					panic("exact search returned fewer than ten points")
				}
				boundary := task.Truth[9].Score
				seenExact := map[uint64]bool{}
				for _, p := range native {
					if seenExact[p.ID] || math.IsNaN(p.Score) || math.IsInf(p.Score, 0) {
						panic("duplicate exact ID or invalid exact score")
					}
					seenExact[p.ID] = true
					if p.ID >= uint64(count()) {
						panic("unexpected exact ID")
					}
					actual := 0.0
					for d, v := range baseCache[p.ID] {
						actual += float64(v) * float64(task.Vector[d])
					}
					if math.Abs(actual-p.Score) > 1e-5 {
						panic("native exact score differs from independent dot product")
					}
					if p.Score < boundary-1e-5 {
						panic("native exact result violates the independent dot-product oracle")
					}
					if p.ID >= uint64(count()) || p.Payload.Kind != "base" || (task.Tenant != nil && p.Payload.Tenant != *task.Tenant) {
						panic("exact search crossed filter boundary")
					}
				}
			}
			for _, variant := range variants {
				_, err := search(hosts()[0], mode, queryBody(tasks[0].Vector, tasks[0].Tenant, false, variant.Quantized, variant.Rescore, variant.Oversampling))
				check(err)
				var latencies []float64
				matched := 0
				for i, task := range tasks {
					host := hosts()[i%len(hosts())]
					begin := time.Now()
					points, err := search(host, mode, queryBody(task.Vector, task.Tenant, false, variant.Quantized, variant.Rescore, variant.Oversampling))
					check(err)
					latencies = append(latencies, float64(time.Since(begin))/float64(time.Millisecond))
					check(validateHits(points, task.Tenant))
					known := map[uint64]bool{}
					for _, p := range task.Truth {
						known[p.ID] = true
					}
					for _, p := range points {
						if known[p.ID] {
							matched++
						}
					}
				}
				// Concurrent wall-clock throughput is separate from the serial recall sample.
				var completed, errors atomic.Uint64
				var workers sync.WaitGroup
				started := time.Now()
				deadline := started.Add(duration)
				for worker := 0; worker < concurrency; worker++ {
					workers.Add(1)
					go func(worker int) {
						defer workers.Done()
						for sequence := worker; time.Now().Before(deadline); sequence += concurrency {
							task := tasks[sequence%len(tasks)]
							points, err := search(hosts()[sequence%len(hosts())], mode, queryBody(task.Vector, task.Tenant, false, variant.Quantized, variant.Rescore, variant.Oversampling))
							if err == nil {
								err = validateHits(points, task.Tenant)
							}
							if err != nil {
								errors.Add(1)
							} else {
								completed.Add(1)
							}
						}
					}(worker)
				}
				workers.Wait()
				elapsed := time.Since(started).Seconds()
				if errors.Load() != 0 {
					panic("concurrent search errors")
				}
				sort.Float64s(latencies)
				out[mode+"/"+filter+"/"+variant.Name] = map[string]any{"queries": queries, "warmup_requests": 1, "k": 10, "recall_at_10": float64(matched) / float64(queries*10), "p50_ms": latencies[len(latencies)/2], "p99_ms": latencies[min(len(latencies)-1, int(float64(len(latencies))*.99))], "qps": float64(completed.Load()) / elapsed, "concurrent_requests": completed.Load(), "concurrency": concurrency, "throughput_seconds": elapsed, "errors": errors.Load(), "rescore": variant.Rescore, "oversampling": variant.Oversampling, "hnsw_ef": number("HNSW_EF", 128, 16, 2048)}
			}
		}
		var memory any
		check(call("q0", "GET", "/collections/"+mode+"/memory", nil, &memory))
		save("memory-"+mode, memory)
	}
	save("query-benchmark", out)
}
