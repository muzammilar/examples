package main

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"time"
)

type collectionInfo struct {
	Status    string          `json:"status"`
	Points    uint64          `json:"points_count"`
	Indexed   uint64          `json:"indexed_vectors_count"`
	Optimizer json.RawMessage `json:"optimizer_status"`
}

func setup() {
	if _, err := os.Stat(filepath.Join(results, "setup.json")); err == nil {
		return
	} else if !os.IsNotExist(err) {
		check(err)
	}
	root, err := raw("q0", "GET", "/", nil)
	check(err)
	var version struct {
		Version string `json:"version"`
	}
	check(json.Unmarshal(root, &version))
	if version.Version != "1.18.3" {
		panic("this example pins Qdrant 1.18.3 API/quantizer behavior")
	}
	for _, mode := range modes {
		config := map[string]any{"vectors": map[string]any{"size": dimension(), "distance": "Dot", "on_disk": mode != "float-ram"}, "sharding_method": "custom", "shard_number": 1, "replication_factor": 3, "write_consistency_factor": 2, "on_disk_payload": true, "hnsw_config": map[string]any{"m": 16, "ef_construct": 128, "on_disk": false}, "optimizers_config": map[string]any{"indexing_threshold": 0, "default_segment_number": 1, "max_optimization_threads": 1}}
		if mode == "scalar8" {
			config["quantization_config"] = map[string]any{"scalar": map[string]any{"type": "int8", "quantile": .99, "always_ram": true}}
		}
		if mode == "turbo4" {
			config["quantization_config"] = map[string]any{"turbo": map[string]any{"bits": "bits4", "always_ram": true}}
		}
		check(call("q0", "PUT", "/collections/"+mode, config, nil))
		for shard := 0; shard < 3; shard++ {
			check(call("q0", "PUT", "/collections/"+mode+"/shards", map[string]any{"shard_key": fmt.Sprintf("customers-%d", shard), "shards_number": 1, "replication_factor": 3}, nil))
		}
		awaitShards(mode)
		for key, kind := range map[string]string{"tenant": "integer", "kind": "keyword"} {
			check(call("q0", "PUT", "/collections/"+mode+"/index?wait=true", map[string]any{"field_name": key, "field_schema": kind}, nil))
		}
	}
	save("setup", map[string]any{"version": version.Version, "points_per_collection": count(), "dimensions": dimension(), "shard_keys": 3, "copies_per_shard": 3, "write_consistency_factor": 2, "metric": "Dot; client-generated unit vectors, stored original float32 values unchanged", "corpus": "deterministic clustered/anisotropic synthetic embeddings; not a semantic relevance corpus"})
}
func seed() {
	if _, err := os.Stat(filepath.Join(results, "seed.json")); !os.IsNotExist(err) {
		panic("existing seed; use the complete clean lifecycle")
	}
	file, err := os.OpenFile(filepath.Join(results, "seed-journal.jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	out := map[string]any{}
	for _, mode := range modes {
		start := time.Now()
		var times []float64
		for first := 0; first < count(); first += 1000 {
			batches := map[string][]point{}
			for i := first; i < min(first+1000, count()); i++ {
				p := makePoint(uint64(i), "base")
				batches[group(p.ID)] = append(batches[group(p.ID)], p)
			}
			for shard := 0; shard < 3; shard++ {
				key := fmt.Sprintf("customers-%d", shard)
				batch := batches[key]
				if len(batch) == 0 {
					continue
				}
				record(file, map[string]any{"collection": mode, "stage": "attempt", "first": first, "shard_key": key, "count": len(batch), "at": time.Now().UTC()})
				began := time.Now()
				check(upsert(mode, key, batch))
				times = append(times, float64(time.Since(began))/float64(time.Millisecond))
				record(file, map[string]any{"collection": mode, "stage": "ack", "first": first, "shard_key": key, "count": len(batch), "at": time.Now().UTC()})
			}
		}
		ingestSeconds := time.Since(start).Seconds()
		check(call("q0", "PATCH", "/collections/"+mode, map[string]any{"optimizers_config": map[string]any{"indexing_threshold": 1}}, nil))
		end := time.Now().Add(15 * time.Minute)
		var info collectionInfo
		for time.Now().Before(end) {
			check(call("q0", "GET", "/collections/"+mode, nil, &info))
			if info.Status == "green" && info.Points == uint64(count()) && info.Indexed >= uint64(count()) {
				break
			}
			time.Sleep(2 * time.Second)
		}
		if info.Status != "green" || info.Points != uint64(count()) || info.Indexed < uint64(count()) {
			panic(fmt.Sprintf("index did not fully converge: %+v", info))
		}
		check(call("q0", "PATCH", "/collections/"+mode, map[string]any{"optimizers_config": map[string]any{"indexing_threshold": 10000}}, nil))
		sort.Float64s(times)
		out[mode] = map[string]any{"points": count(), "ingest_seconds": ingestSeconds, "points_per_second": float64(count()) / ingestSeconds, "ready_seconds": time.Since(start).Seconds(), "requests": len(times), "request_p50_ms": times[len(times)/2], "request_p99_ms": times[min(len(times)-1, int(float64(len(times))*.99))], "indexed_vectors": info.Indexed}
	}
	save("seed", out)
}

var coordinator = "q0"

func upsert(collection, shard string, batch []point) error {
	var result struct {
		Status string `json:"status"`
	}
	err := call(coordinator, "PUT", "/collections/"+collection+"/points?wait=true&ordering=medium", map[string]any{"shard_key": shard, "points": batch}, &result)
	if err != nil { // Change the endpoint only for the next distinct operation; no mutation replay.
		for _, host := range []string{"q0", "q1", "q2"} {
			if _, probe := raw(host, "GET", "/readyz", nil); probe == nil {
				coordinator = host
				break
			}
		}
		return err
	}
	if result.Status != "completed" {
		return fmt.Errorf("upsert not applied: %+v", result)
	}
	return nil
}

func ready() {
	end := time.Now().Add(180 * time.Second)
	for time.Now().Before(end) {
		leaders := map[uint64]bool{}
		good := true
		for _, host := range hosts() {
			var native struct {
				Peer  uint64 `json:"peer_id"`
				Peers map[string]struct {
					URI string `json:"uri"`
				} `json:"peers"`
				Raft struct {
					Leader *uint64 `json:"leader"`
				} `json:"raft_info"`
			}
			if err := call(host, "GET", "/cluster", nil, &native); err != nil || len(native.Peers) != len(hosts()) || native.Raft.Leader == nil {
				good = false
				break
			}
			if _, ok := native.Peers[strconv.FormatUint(native.Peer, 10)]; !ok {
				good = false
				break
			}
			leaders[*native.Raft.Leader] = true
		}
		if good && len(leaders) == 1 {
			return
		}
		time.Sleep(time.Second)
	}
	panic("native Raft membership did not converge")
}

func awaitShards(collection string) {
	deadline := time.Now().Add(180 * time.Second)
	for time.Now().Before(deadline) {
		good := true
		for _, host := range hosts() {
			var native clusterInfo
			if err := call(host, "GET", "/collections/"+collection+"/cluster", nil, &native); err != nil || len(native.Local) != 3 || len(native.Transfers) != 0 {
				good = false
				break
			}
			for _, shard := range native.Local {
				if shard.State != "Active" {
					good = false
				}
			}
		}
		if good {
			return
		}
		time.Sleep(time.Second)
	}
	panic("native customer shards did not become Active")
}
