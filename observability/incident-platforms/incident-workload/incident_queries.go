package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"time"
)

// These counts supplement, rather than replace, the exact full-body oracle.
func incidentQueries(ctx context.Context, f Fixture, report string) error {
	if f.Requests != 10000 || f.MetricPoints != 100 || f.LogsPerRequest != 100 {
		return errors.New("incident query fixture must be the complete bounded corpus")
	}
	queries := []struct {
		name, kind, stream, predicate string
		want                          int64
	}{
		{"incident-error-logs", "logs", "default", "tenant='tenant-0' AND severity_number=17", 25000},
		{"incident-error-spans", "traces", "default", "tenant='tenant-0' AND span_status='ERROR'", 500},
		{"incident-slow-points", "metrics", "checkout_latency_microseconds", "tenant='tenant-0' AND value>=50000", 1250},
	}
	rows := []map[string]any{}
	for _, query := range queries {
		for repetition := 0; repetition <= 10; repetition++ {
			payload := map[string]any{"use_cache": false, "query": map[string]any{
				"sql":        fmt.Sprintf(`SELECT COUNT(*) AS records FROM "%s" WHERE run='%s' AND (%s)`, query.stream, f.Run, query.predicate),
				"start_time": f.StartMS*1000 - 1, "end_time": (f.StartMS + int64(f.Requests)*10 + 100000) * 1000,
				"from": 0, "size": 1,
			}}
			started := time.Now()
			raw, err := openObserveJSON(ctx, "POST", "/api/default/_search?type="+query.kind, payload)
			elapsed := time.Since(started)
			if err != nil {
				return err
			}
			var response struct {
				Hits             []nativeRow `json:"hits"`
				Partial          bool        `json:"is_partial"`
				ResultCacheRatio float64     `json:"result_cache_ratio"`
			}
			if err = json.Unmarshal(raw, &response); err != nil {
				return err
			}
			if response.Partial || response.ResultCacheRatio != 0 || len(response.Hits) != 1 {
				return errors.New("incident query returned partial, cached or malformed result")
			}
			actual, err := integerField(response.Hits[0], "records")
			if err != nil || actual != query.want {
				return fmt.Errorf("%s count=%s expected=%d", query.name, response.Hits[0]["records"], query.want)
			}
			rows = append(rows, map[string]any{"query": query.name, "repetition": repetition, "first_in_fresh_worker": repetition == 0, "http_body_ms": float64(elapsed.Nanoseconds()) / 1e6, "records": actual})
		}
	}
	file, err := os.OpenFile(report, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	err = json.NewEncoder(file).Encode(map[string]any{
		"backend": "openobserve", "fixture": f, "queries": rows, "passed": true,
		"scope":  "Native incident SQL after full-value qualification; result cache disabled; OS/engine caches retained. First access by a fresh worker is not physical-cold storage.",
		"timing": "Go monotonic HTTP request plus response body, ms; source encoding and result parsing excluded",
	})
	return errors.Join(err, file.Sync(), file.Close())
}
