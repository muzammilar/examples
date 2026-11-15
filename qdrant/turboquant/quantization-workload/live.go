package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

func caseName() string {
	name := os.Getenv("FAULT_CASE")
	if name == "" || filepath.Base(name) != name {
		panic("invalid FAULT_CASE")
	}
	return name
}
func stopped(name string) bool {
	_, err := os.Stat(filepath.Join(results, "stop-"+name))
	if err != nil && !os.IsNotExist(err) {
		check(err)
	}
	return err == nil
}
func churn() {
	client.Timeout = 10 * time.Second
	name := caseName()
	file, err := os.OpenFile(filepath.Join(results, "fault-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	first := uint64(time.Now().UnixMilli()) * 1000
	for batch := 0; batch < 18000; batch++ {
		if stopped(name) {
			return
		}
		groups := map[string][]point{}
		for offset := 0; offset < 100; offset++ {
			p := makePoint(first+uint64(batch*100+offset), "recovery")
			key := group(p.ID)
			groups[key] = append(groups[key], p)
		}
		for shard := 0; shard < 3; shard++ {
			key := fmt.Sprintf("customers-%d", shard)
			points := groups[key]
			ids := make([]uint64, len(points))
			for i, p := range points {
				ids[i] = p.ID
			}
			record(file, map[string]any{"stage": "attempt", "ids": ids, "count": len(ids), "shard_key": key, "at": time.Now().UTC()})
			started := time.Now()
			err := upsert("turbo4", key, points)
			stage, message := "ack", ""
			if err != nil {
				stage, message = "ambiguous", err.Error()
			}
			record(file, map[string]any{"stage": stage, "ids": ids, "count": len(ids), "shard_key": key, "at": time.Now().UTC(), "latency_ms": float64(time.Since(started)) / float64(time.Millisecond), "error": message})
		}
		time.Sleep(time.Second)
	}
	panic("writer expired before recovery")
}
func watch() {
	client.Timeout = 10 * time.Second
	name := caseName()
	file, err := os.OpenFile(filepath.Join(results, "reads-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	host := "q0"
	for sequence := 0; sequence < 18000; sequence++ {
		if stopped(name) {
			return
		}
		id := uint64((sequence * 7919) % count())
		started := time.Now().UTC()
		outcome, message := "ok", ""
		var points []point
		err = call(host, "POST", "/collections/turbo4/points?consistency=quorum&timeout=5", map[string]any{"ids": []uint64{id}, "with_payload": true, "with_vector": true, "shard_key": group(id)}, &points)
		if err != nil {
			outcome, message = "error", err.Error()
		} else if len(points) == 0 {
			outcome = "missing"
		} else if len(points) != 1 || points[0].ID != id {
			outcome = "wrong"
		} else if e := valid(points[0], group(id)); e != nil {
			outcome, message = "wrong", e.Error()
		} else {
			tenant := int(id % 100)
			hits, e := search(host, "turbo4", queryBody(embedding(uint64(count()+sequence)), &tenant, false, true, false, 1))
			if e != nil {
				outcome, message = "error", e.Error()
			} else if len(hits) != 10 {
				outcome = "wrong"
				message = "filtered ANN returned fewer than k"
			} else {
				for _, hit := range hits {
					if hit.ID >= uint64(count()) || hit.Payload.Kind != "base" || hit.Payload.Tenant != tenant || int(hit.ID%100) != tenant {
						outcome, message = "wrong", "tenant/base filter isolation failed"
					}
				}
			}
		}
		if outcome == "error" {
			for _, next := range hosts() {
				if _, e := raw(next, "GET", "/readyz", nil); e == nil {
					host = next
					break
				}
			}
		}
		record(file, map[string]any{"started_at": started, "at": time.Now().UTC(), "outcome": outcome, "point_id": id, "latency_ms": float64(time.Since(started)) / float64(time.Millisecond), "error": message, "operation": "known point body/vector read followed by filtered ANN; no fresh-write visibility claim"})
		time.Sleep(100 * time.Millisecond)
	}
	panic("reader expired before recovery")
}
