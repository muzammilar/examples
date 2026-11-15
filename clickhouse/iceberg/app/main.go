package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type injectedFailure string

func (e injectedFailure) Error() string { return string(e) }

var readSnapshot int64

var client = &http.Client{Timeout: 5 * time.Minute}

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func number(k string, d int64) (int64, error) {
	n, e := strconv.ParseInt(env(k, strconv.FormatInt(d, 10)), 10, 64)
	if e != nil || n < 1 || n > 100000000 {
		return 0, fmt.Errorf("%s must be 1..100000000", k)
	}
	return n, nil
}
func request(ctx context.Context, method, u, content, token string, body []byte) ([]byte, int, error) {
	r, e := http.NewRequestWithContext(ctx, method, u, bytes.NewReader(body))
	if e != nil {
		return nil, 0, e
	}
	if content != "" {
		r.Header.Set("Content-Type", content)
	}
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	resp, e := client.Do(r)
	if e != nil {
		return nil, 0, e
	}
	defer resp.Body.Close()
	b, e := io.ReadAll(io.LimitReader(resp.Body, 1048577))
	if e != nil {
		return nil, resp.StatusCode, e
	}
	if len(b) > 1048576 {
		return nil, resp.StatusCode, fmt.Errorf("response exceeds 1 MiB")
	}
	if resp.StatusCode >= 300 {
		return b, resp.StatusCode, fmt.Errorf("HTTP %d: %s", resp.StatusCode, b)
	}
	return b, resp.StatusCode, nil
}
func sql(ctx context.Context, q string) (string, error) {
	u := env("CLICKHOUSE_URL", "http://clickhouse:8123") + "/?allow_database_iceberg=1&allow_insert_into_iceberg=1&max_execution_time=120&max_threads=2&max_memory_usage=805306368&max_rows_to_read=100000000&max_result_rows=10000"
	r, e := http.NewRequestWithContext(ctx, "POST", u, strings.NewReader(q))
	if e != nil {
		return "", e
	}
	r.SetBasicAuth("default", "local-reader")
	resp, e := client.Do(r)
	if e != nil {
		return "", e
	}
	defer resp.Body.Close()
	b, e := io.ReadAll(io.LimitReader(resp.Body, 1048577))
	if e != nil {
		return "", e
	}
	if len(b) > 1048576 {
		return "", fmt.Errorf("query result exceeds 1 MiB")
	}
	if resp.StatusCode != 200 {
		return "", fmt.Errorf("ClickHouse HTTP %d: %s", resp.StatusCode, b)
	}
	return strings.TrimSpace(string(b)), nil
}
func exec(ctx context.Context, q string) error { _, e := sql(ctx, q); return e }
func rest(ctx context.Context, method, path, token string, v any) ([]byte, int, error) {
	var b []byte
	var e error
	if v != nil {
		b, e = json.Marshal(v)
		if e != nil {
			return nil, 0, e
		}
	}
	return request(ctx, method, "http://storage:8181/v1"+path, "application/json", token, b)
}
func token(ctx context.Context) (string, error) {
	form := url.Values{"grant_type": {"client_credentials"}, "client_id": {"demo"}, "client_secret": {"demo"}}
	b, _, e := request(ctx, "POST", "http://storage:8181/v1/oauth/tokens", "application/x-www-form-urlencoded", "", []byte(form.Encode()))
	if e != nil {
		return "", e
	}
	var t struct {
		Token string `json:"access_token"`
	}
	if e = json.Unmarshal(b, &t); e != nil {
		return "", e
	}
	if t.Token == "" {
		return "", fmt.Errorf("empty catalog token")
	}
	return t.Token, nil
}
func bootstrap(ctx context.Context, archive bool) error {
	t, e := token(ctx)
	if e != nil {
		return e
	}
	_, code, e := rest(ctx, "GET", "/namespaces/lab", t, nil)
	if code == 404 {
		_, _, e = rest(ctx, "POST", "/namespaces", t, map[string]any{"namespace": []string{"lab"}})
	}
	if e != nil {
		return e
	}
	// Each test resets only its own table. Snapshot retention is within a test run.
	_, code, e = rest(ctx, "DELETE", "/namespaces/lab/tables/events", t, nil)
	if e != nil && code != 404 {
		return e
	}
	fields := []any{map[string]any{"id": 1, "name": "event_id", "required": true, "type": "long"}, map[string]any{"id": 2, "name": "customer_id", "required": true, "type": "int"}, map[string]any{"id": 3, "name": "amount_cents", "required": true, "type": "long"}}
	if archive {
		fields = append(fields, map[string]any{"id": 4, "name": "event_day", "required": true, "type": "date"})
	}
	_, _, e = rest(ctx, "POST", "/namespaces/lab/tables", t, map[string]any{"name": "events", "location": "s3://warehouse/lab/events", "schema": map[string]any{"type": "struct", "schema-id": 0, "fields": fields}, "properties": map[string]string{"format-version": "2"}})
	if e != nil {
		return e
	}
	return exec(ctx, "CREATE DATABASE IF NOT EXISTS lake ENGINE=DataLakeCatalog('http://storage:8181','demo','demo') SETTINGS catalog_type='rest', catalog_credential='demo:demo', vended_credentials=false, warehouse='warehouse', storage_endpoint='http://storage:8333/warehouse'")
}
func total(n int64) int64 { return n/10000*50005000 + (n%10000)*(n%10000+1)/2 }
func check(ctx context.Context, table string, lo, hi, customers int64) error {
	q := fmt.Sprintf("SELECT count(),uniqExact(event_id),sum(amount_cents),countIf(event_id IS NULL OR customer_id IS NULL OR amount_cents IS NULL OR event_id<%d OR event_id>=%d OR customer_id!=event_id%%%d OR amount_cents!=event_id%%10000+1) FROM %s FORMAT TabSeparated", lo, hi, customers, table)
	if readSnapshot > 0 {
		q = strings.Replace(q, " FORMAT TabSeparated", fmt.Sprintf(" SETTINGS iceberg_snapshot_id=%d FORMAT TabSeparated", readSnapshot), 1)
	}
	s, e := sql(ctx, q)
	if e != nil {
		return e
	}
	want := fmt.Sprintf("%d\t%d\t%d\t0", hi-lo, hi-lo, total(hi)-total(lo))
	if s != want {
		return fmt.Errorf("%s oracle: got %q want %q", table, s, want)
	}
	return nil
}
func snapshot(ctx context.Context) (int64, error) {
	t, e := token(ctx)
	if e != nil {
		return 0, e
	}
	b, _, e := rest(ctx, "GET", "/namespaces/lab/tables/events", t, nil)
	if e != nil {
		return 0, e
	}
	var v struct {
		Metadata struct {
			Snapshot int64 `json:"current-snapshot-id"`
		} `json:"metadata"`
	}
	if e = json.Unmarshal(b, &v); e != nil {
		return 0, e
	}
	return v.Metadata.Snapshot, nil
}
func output(name string, v any) error {
	b, e := json.MarshalIndent(v, "", "  ")
	if e != nil {
		return e
	}
	fmt.Println(string(b))
	return os.WriteFile(filepath.Join(env("RESULTS_DIR", "/results"), name+".json"), append(b, '\n'), 0644)
}
func run(ctx context.Context, mode string) error {
	if mode == "benchmark" {
		return benchmark(ctx)
	}
	n, e := number("ROWS", 2000000)
	if e != nil {
		return e
	}
	batch, e := number("BATCH_ROWS", 100000)
	if e != nil {
		return e
	}
	customers, e := number("CUSTOMERS", 10000)
	if e != nil {
		return e
	}
	if mode == "verify-snapshot" {
		b, e := os.ReadFile(filepath.Join(env("RESULTS_DIR", "/results"), "direct.json"))
		if e != nil {
			return e
		}
		var v struct {
			Snapshot int64 `json:"snapshot"`
		}
		if e = json.Unmarshal(b, &v); e != nil {
			return e
		}
		if v.Snapshot <= 0 {
			return fmt.Errorf("missing retained snapshot")
		}
		readSnapshot = v.Snapshot
		if e = check(ctx, "lake.`lab.events`", 0, n, customers); e != nil {
			return e
		}
		return output("snapshot-read", map[string]any{"snapshot": v.Snapshot, "rows": n, "missing_ids": 0, "duplicate_ids": 0})
	}
	if mode == "append" {
		q := fmt.Sprintf("INSERT INTO lake.`lab.events` SELECT toInt64(number),toInt32(number%%%d),toInt64(number%%10000+1) FROM numbers(%d,%d)", customers, n, batch)
		if e = exec(ctx, q); e != nil {
			return fmt.Errorf("unresolved append; do not blindly retry: %w", e)
		}
		if e = check(ctx, "lake.`lab.events`", 0, n+batch, customers); e != nil {
			return e
		}
		return output("append", map[string]any{"rows": n + batch, "missing_ids": 0, "duplicate_ids": 0})
	}

	if mode == "verify" || mode == "verify-archive" {
		if mode == "verify-archive" {
			n /= 2
		}
		return check(ctx, "lake.`lab.events`", 0, n, customers)
	}
	if mode == "assert-retained" {
		return check(ctx, "default.hot", 0, n, customers)
	}
	if mode == "resume-archive" {
		result := map[string]any{"rows": n, "resumed": true}
		if e = finishArchive(ctx, n, batch, customers, result); e != nil {
			return e
		}
		return output("archive-resume", result)
	}
	if mode == "archive" && n < 2 {
		return fmt.Errorf("archive requires ROWS>=2")
	}
	if mode != "direct" && mode != "archive" {
		return fmt.Errorf("usage: lab direct|archive|verify")
	}
	// Discover this example's single server before schema changes.
	for _, q := range []string{"SELECT name FROM system.databases LIMIT 100", "SELECT name,type FROM system.columns WHERE database='default' LIMIT 100"} {
		if _, e = sql(ctx, q); e != nil {
			return e
		}
	}
	if e = bootstrap(ctx, mode == "archive"); e != nil {
		return e
	}
	start := time.Now()
	journal, e := os.OpenFile("/results/acknowledgements.jsonl", os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	if e != nil {
		return e
	}
	defer journal.Close()
	destination := "lake.`lab.events`"
	if mode == "archive" {
		for _, q := range []string{"DROP TABLE IF EXISTS default.hot", "CREATE TABLE default.hot (event_id Int64,customer_id Int32,amount_cents Int64,event_day Date,expire_at DateTime DEFAULT toDateTime('2100-01-01 00:00:00')) ENGINE=MergeTree PARTITION BY event_day ORDER BY (customer_id,event_day,event_id) TTL expire_at DELETE SETTINGS merge_with_ttl_timeout=1"} {
			if e = exec(ctx, q); e != nil {
				return e
			}
		}
		destination = "default.hot (event_id,customer_id,amount_cents,event_day)"
	}
	for lo := int64(0); lo < n; lo += batch {
		hi := min(lo+batch, n)
		extra := ""
		if mode == "archive" {
			extra = fmt.Sprintf(",if(number<%d,toDate('2020-01-01'),toDate('2099-01-01'))", n/2)
		}
		q := fmt.Sprintf("INSERT INTO %s SELECT toInt64(number),toInt32(number%%%d),toInt64(number%%10000+1)%s FROM numbers(%d,%d)", destination, customers, extra, lo, hi-lo)
		batchStart := time.Now()
		if e = exec(ctx, q); e != nil {
			return fmt.Errorf("unresolved append [%d,%d); never blindly retry: %w", lo, hi, e)
		}
		if _, e = fmt.Fprintf(journal, "{\"first_id\":%d,\"end_id\":%d,\"seconds\":%.9f}\n", lo, hi, time.Since(batchStart).Seconds()); e != nil {
			return e
		}
		if e = journal.Sync(); e != nil {
			return e
		}
	}
	result := map[string]any{"rows": n, "customers": customers, "batch_rows": batch, "load_seconds": time.Since(start).Seconds(), "mode": mode}
	if mode == "archive" {
		if e = finishArchive(ctx, n, batch, customers, result); e != nil {
			return e
		}
	} else if e = check(ctx, "lake.`lab.events`", 0, n, customers); e != nil {
		return e
	}

	snap, e := snapshot(ctx)
	if e != nil {
		return e
	}
	result["snapshot"] = snap
	result["amount_cents"] = total(n)
	result["missing_ids"] = 0
	result["duplicate_ids"] = 0
	version, e := sql(ctx, "SELECT version()")
	if e != nil {
		return e
	}
	result["clickhouse"] = version
	return output(mode, result)
}
func finishArchive(ctx context.Context, n, batch, customers int64, result map[string]any) error {
	var e error

	if env("FAIL_AT", "") == "before-export" {
		return injectedFailure("injected before-export failure; hot data retained")
	}
	// The old partition is sealed by this single-owner test. A production writer
	// must enforce the same boundary; a TTL alone cannot coordinate the handoff.
	cut := n / 2
	existing, e := sql(ctx, "SELECT count() FROM lake.`lab.events`")
	if e != nil {
		return e
	}
	if existing == "0" {
		for lo := int64(0); lo < cut; lo += batch {
			hi := min(lo+batch, cut)
			q := fmt.Sprintf("INSERT INTO lake.`lab.events` SELECT event_id,customer_id,amount_cents,event_day FROM default.hot WHERE event_id>=%d AND event_id<%d", lo, hi)
			if e = exec(ctx, q); e != nil {
				return fmt.Errorf("unresolved archive commit; hot data retained, no automatic replay: %w", e)
			}
		}
	} // Otherwise verify the prior committed archive; never re-append it.

	if env("FAIL_AT", "") == "verification" {
		return injectedFailure("injected verification failure; hot data retained")
	}
	// Verify through a newly resolved catalog table before authorizing expiry.
	if e = check(ctx, "lake.`lab.events`", 0, cut, customers); e != nil {
		return e
	}
	invalidDates, e := sql(ctx, "SELECT countIf(event_day!=toDate('2020-01-01')) FROM lake.`lab.events`")
	if e != nil {
		return e
	}
	if invalidDates != "0" {
		return fmt.Errorf("archive event dates changed: %s invalid rows", invalidDates)
	}

	if env("FAIL_AT", "") == "after-export" {
		return injectedFailure("injected after-export failure; hot data retained")
	}
	snap, e := snapshot(ctx)
	if e != nil {
		return e
	}
	if e = output("archive-authorization", map[string]any{"snapshot": snap, "archived_rows": cut}); e != nil {
		return e
	}
	// Rare sealed-partition handoff, not a per-event mutation. TTL does deletion.
	t := time.Now()
	if e = exec(ctx, "ALTER TABLE default.hot UPDATE expire_at=toDateTime('2000-01-01 00:00:00') WHERE event_day=toDate('2020-01-01') SETTINGS mutations_sync=2"); e != nil {
		return e
	}
	if e = exec(ctx, "ALTER TABLE default.hot MATERIALIZE TTL SETTINGS mutations_sync=2"); e != nil {
		return e
	}
	if e = check(ctx, "default.hot", cut, n, customers); e != nil {
		return e
	}
	if e = check(ctx, "lake.`lab.events`", 0, cut, customers); e != nil {
		return e
	}
	if e = check(ctx, "(SELECT event_id,customer_id,amount_cents FROM default.hot WHERE event_day>=toDate('2099-01-01') UNION ALL SELECT event_id,customer_id,amount_cents FROM lake.`lab.events`)", 0, n, customers); e != nil {
		return e
	}
	result["archived_rows"] = cut
	result["hot_rows"] = n - cut
	result["ttl_seconds"] = time.Since(t).Seconds()
	return nil
}

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	if len(os.Args) != 2 {
		log.Fatal("usage: lab direct|archive|verify")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Minute)
	defer cancel()
	if e := run(ctx, os.Args[1]); e != nil {
		log.Print(e)
		var injected injectedFailure
		if errors.As(e, &injected) {
			os.Exit(42)
		}
		os.Exit(1)
	}
}
