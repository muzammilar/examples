package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type shardInfo struct {
	ID    uint32 `json:"shard_id"`
	Key   string `json:"shard_key"`
	Count uint64 `json:"points_count"`
	State string `json:"state"`
}
type clusterInfo struct {
	Peer      uint64            `json:"peer_id"`
	Local     []shardInfo       `json:"local_shards"`
	Remote    []json.RawMessage `json:"remote_shards"`
	Transfers []json.RawMessage `json:"shard_transfers"`
}

var baseCache [][]float32
var baseDigests []string

func cacheBase() {
	if baseCache != nil {
		return
	}
	baseCache = make([][]float32, count())
	baseDigests = make([]string, count())
	for i := range baseCache {
		baseCache[i] = embedding(uint64(i))
		baseDigests[i] = digest(uint64(i), baseCache[i])
	}
}
func hosts() []string { return strings.Split(env("QDRANT_HOSTS", "q0,q1,q2"), ",") }
func history() (map[uint64]bool, map[uint64]bool, int) {
	attempted, acked := map[uint64]bool{}, map[uint64]bool{}
	files, err := filepath.Glob(filepath.Join(results, "fault-*.jsonl"))
	check(err)
	for _, path := range files {
		file, err := os.Open(path)
		check(err)
		scanner := bufio.NewScanner(file)
		for scanner.Scan() {
			var r struct {
				Stage string   `json:"stage"`
				IDs   []uint64 `json:"ids"`
			}
			check(json.Unmarshal(scanner.Bytes(), &r))
			for _, id := range r.IDs {
				if r.Stage == "attempt" {
					attempted[id] = true
				}
				if r.Stage == "ack" {
					acked[id] = true
				}
			}
		}
		check(scanner.Err())
		check(file.Close())
	}
	return attempted, acked, len(files)
}
func verify(full bool) {
	if full {
		cacheBase()
	}
	attempted, acked, cases := history()
	copies := map[string]map[string]int{}
	out := map[string]any{}
	for _, mode := range modes {
		if !full && mode != "turbo4" {
			continue
		}
		copies[mode] = map[string]int{}
		for _, host := range hosts() {
			var native clusterInfo
			check(call(host, "GET", "/collections/"+mode+"/cluster", nil, &native))
			if len(native.Transfers) != 0 {
				panic("native shard transfer still running")
			}
			for _, shard := range native.Local {
				if shard.State != "Active" {
					panic("non-active local replica")
				}
				copies[mode][shard.Key]++
				seen := map[uint64]bool{}
				seenBase, seenRecovery := 0, 0
				var offset *uint64
				for {
					request := map[string]any{"limit": 250, "with_payload": true, "with_vector": true}
					if offset != nil {
						request["offset"] = *offset
					}
					if !full {
						request["filter"] = map[string]any{"must": []any{map[string]any{"key": "kind", "match": map[string]any{"value": "recovery"}}}}
					}
					var page struct {
						Points []point `json:"points"`
						Next   *uint64 `json:"next_page_offset"`
					}
					// Pinned 1.18 local-shard REST handler selects ShardId, hence local_only;
					// these reads cannot be silently routed to a healthy copy on another peer.
					check(call(host, "POST", fmt.Sprintf("/collections/%s/shards/%d/points/scroll?timeout=120", mode, shard.ID), request, &page))
					for _, p := range page.Points {
						if seen[p.ID] {
							panic("duplicate point on a physical replica")
						}
						seen[p.ID] = true
						check(valid(p, shard.Key))
						if p.ID < uint64(count()) {
							seenBase++
						} else {
							if mode != "turbo4" || !attempted[p.ID] {
								panic("unwitnessed recovery point")
							}
							seenRecovery++
						}
					}
					if page.Next == nil {
						break
					}
					offset = page.Next
				}
				expectedBase := 0
				for i := 0; i < count(); i++ {
					if group(uint64(i)) == shard.Key {
						expectedBase++
					}
				}
				if full && seenBase != expectedBase {
					panic(fmt.Sprintf("missing base points %s/%s/%s: %d/%d", mode, host, shard.Key, seenBase, expectedBase))
				}
				if mode == "turbo4" {
					for id := range acked {
						if group(id) == shard.Key && !seen[id] {
							panic(fmt.Sprintf("lost acknowledged point %d on %s", id, host))
						}
					}
				}
				if shard.Count != uint64(expectedBase+seenRecovery) {
					panic("native local point count disagrees with full contents")
				}
				out[mode+"/"+host+"/"+shard.Key] = map[string]any{"shard_id": shard.ID, "peer_id": native.Peer, "base_points_checked": seenBase, "recovery_points_checked": seenRecovery, "native_count": shard.Count}
			}
		}
		if len(copies[mode]) != 3 {
			panic("expected three customer shard keys")
		}
		for _, n := range copies[mode] {
			if n != 3 {
				panic("customer shard does not have exactly three physical copies")
			}
		}
	}
	save("recovery-history", map[string]any{"cases": cases, "acknowledged_points": len(acked), "attempted_points": len(attempted), "missing_acknowledged": 0, "wrong_values_or_routing": 0, "scope": "every physical local shard; all recovery vector components/payloads; base vectors checked only when full_baseline=true", "full_baseline": full, "copies": copies})
	save("physical-placement", out)
}
