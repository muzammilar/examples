// Deterministic telemetry, official Remote Write protobufs, and an external ACK oracle.
package main

import (
	"bufio"
	"bytes"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/golang/snappy"
	"github.com/prometheus/prometheus/prompb"
)

//go:embed pipeline.yaml
var logPipeline string

var httpClient = &http.Client{Timeout: 120 * time.Second}
var greptime = env("GREPTIME", "http://front0:4000")
var results = env("RESULTS", "/results")

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
func number(k string, d int) int {
	v, e := strconv.Atoi(env(k, strconv.Itoa(d)))
	if e != nil || v < 1 {
		panic("invalid " + k)
	}
	return v
}
func check(e error) {
	if e != nil {
		panic(e)
	}
}
func save(name string, v any) {
	b, e := json.MarshalIndent(v, "", "  ")
	check(e)
	path := filepath.Join(results, name+".json")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	check(err)
	_, err = file.Write(append(b, '\n'))
	check(err)
	check(file.Sync())
	check(file.Close())
	fmt.Println("Saved", path)
}
func request(method, address, kind string, body []byte) []byte {
	b, err := requestE(method, address, kind, body)
	check(err)
	return b
}
func requestE(method, address, kind string, body []byte) ([]byte, error) {
	req, e := http.NewRequest(method, address, bytes.NewReader(body))
	if e != nil {
		return nil, e
	}
	if kind != "" {
		req.Header.Set("Content-Type", kind)
	}
	if kind == "application/x-protobuf" {
		req.Header.Set("Content-Encoding", "snappy")
		req.Header.Set("X-Prometheus-Remote-Write-Version", "0.1.0")
	}
	resp, e := httpClient.Do(req)
	if e != nil {
		return nil, e
	}
	defer resp.Body.Close()
	b, e := io.ReadAll(resp.Body)
	if e != nil {
		return nil, e
	}
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("%s %s: %d %s", method, address, resp.StatusCode, b)
	}
	return b, nil
}
func sql(query string) [][]any {
	rows, err := sqlE(query)
	check(err)
	return rows
}
func sqlE(query string) ([][]any, error) {
	b, err := requestE("POST", greptime+"/v1/sql", "application/x-www-form-urlencoded", []byte(url.Values{"sql": {query}}.Encode()))
	if err != nil {
		return nil, err
	}
	var v struct {
		Code   int    `json:"code"`
		Error  string `json:"error"`
		Output []struct {
			Records struct {
				Rows [][]any `json:"rows"`
			} `json:"records"`
		} `json:"output"`
	}
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, err
	}
	if v.Code != 0 || v.Error != "" {
		return nil, fmt.Errorf("SQL request: %s", b)
	}
	if len(v.Output) == 0 {
		return nil, nil
	}
	return v.Output[0].Records.Rows, nil
}

type fixture struct {
	Series int   `json:"series"`
	Points int   `json:"points"`
	Logs   int   `json:"logs"`
	Start  int64 `json:"start_ms"`
}

func load() fixture {
	var f fixture
	b, e := os.ReadFile(filepath.Join(results, "fixture.json"))
	check(e)
	check(json.Unmarshal(b, &f))
	return f
}
func journal(system, phase string, first, last int) {
	f, e := os.OpenFile(filepath.Join(results, "journal.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	check(e)
	defer f.Close()
	check(json.NewEncoder(f).Encode(map[string]any{"system": system, "phase": phase, "first": first, "last": last, "time": time.Now().UTC()}))
	check(f.Sync())
}
func host(i int) string      { return fmt.Sprintf("h%06d", i) }
func value(h, p int) float64 { return float64(h*1000 + p) }
func payload(f fixture, first, last int) []byte {
	w := prompb.WriteRequest{}
	for h := first; h < last; h++ {
		ts := prompb.TimeSeries{Labels: []prompb.Label{{Name: "__name__", Value: "fleet_load"}, {Name: "host", Value: host(h)}, {Name: "tenant", Value: strconv.Itoa(h % 100)}}}
		for p := 0; p < f.Points; p++ {
			ts.Samples = append(ts.Samples, prompb.Sample{Timestamp: f.Start + int64(p)*1000, Value: value(h, p)})
		}
		w.Timeseries = append(w.Timeseries, ts)
	}
	b, e := w.Marshal()
	check(e)
	return snappy.Encode(nil, b)
}
func writeMetrics(system string, f fixture) map[string]any {
	address := env("VM_WRITE", "http://vminsert:8480/insert/0/prometheus/api/v1/write")
	if system == "greptime" {
		address = greptime + "/v1/prometheus/write?db=public"
	}
	var lat []float64
	var elapsed time.Duration
	for first := 0; first < f.Series; first += 100 {
		last := min(first+100, f.Series)
		body := payload(f, first, last)
		journal(system, "attempt-metrics", first, last)
		start := time.Now()
		request("POST", address, "application/x-protobuf", body)
		d := time.Since(start)
		elapsed += d
		lat = append(lat, float64(d)/float64(time.Millisecond))
		journal(system, "ack-metrics", first, last)
	}
	return stats(lat, elapsed, float64(f.Series*f.Points), "samples")
}
func logRow(f fixture, i int) map[string]any {
	level := "INFO"
	if i%10 == 0 {
		level = "ERROR"
	}
	return map[string]any{"event_id": i, "tenant": strconv.Itoa(i % 100), "level": level, "message": fmt.Sprintf("customer=%d order=%d status=%s", i%100, i, level), "timestamp": time.UnixMilli(f.Start + int64(i)).UTC().Format(time.RFC3339Nano), "epoch_ms": f.Start + int64(i)}
}
func writeLogs(system string, f fixture) map[string]any {
	var lat []float64
	var elapsed time.Duration
	for first := 0; first < f.Logs; first += 1000 {
		last := min(first+1000, f.Logs)
		var body bytes.Buffer
		rows := make([]any, 0, last-first)
		for i := first; i < last; i++ {
			r := logRow(f, i)
			if system == "greptime" {
				rows = append(rows, r)
			} else {
				check(json.NewEncoder(&body).Encode(r))
			}
		}
		address := env("VLOGS", "http://vlogs:9428") + "/insert/jsonline?_stream_fields=tenant&_time_field=timestamp&_msg_field=message"
		kind := "application/stream+json"
		if system == "greptime" {
			b, e := json.Marshal(rows)
			check(e)
			body.Write(b)
			address = greptime + "/v1/ingest?db=public&table=events&pipeline_name=telemetry"
			kind = "application/json"
		}
		journal(system, "attempt-logs", first, last)
		start := time.Now()
		request("POST", address, kind, body.Bytes())
		d := time.Since(start)
		elapsed += d
		lat = append(lat, float64(d)/float64(time.Millisecond))
		journal(system, "ack-logs", first, last)
	}
	return stats(lat, elapsed, float64(f.Logs), "logs")
}
func stats(lat []float64, d time.Duration, n float64, unit string) map[string]any {
	sort.Float64s(lat)
	if len(lat) == 0 {
		panic("empty measurements")
	}
	return map[string]any{"count": n, "unit": unit, "requests": len(lat), "seconds": d.Seconds(), "per_second": n / d.Seconds(), "request_p50_ms": lat[(len(lat)-1)/2], "request_p99_ms": lat[int(math.Ceil(float64(len(lat))*.99))-1], "request_errors": 0}
}
func setup(f fixture) {
	a, b := host(f.Series/3), host(2*f.Series/3)
	partition := fmt.Sprintf("PARTITION ON COLUMNS(host) (host < '%s',host >= '%s' AND host < '%s',host >= '%s')", a, a, b, b)
	if os.Getenv("STANDALONE") == "true" {
		partition = ""
	}
	sql("CREATE TABLE fleet_load (host STRING,tenant STRING,greptime_timestamp TIMESTAMP(3) TIME INDEX,greptime_value DOUBLE,PRIMARY KEY(host,tenant)) " + partition + " ENGINE=mito")
	logPartitions := "PARTITION ON COLUMNS(tenant) (tenant < '33',tenant >= '33' AND tenant < '66',tenant >= '66')"
	if os.Getenv("STANDALONE") == "true" {
		logPartitions = ""
	}
	sql("CREATE TABLE events (event_id BIGINT,tenant STRING,\"level\" STRING,\"message\" STRING,\"timestamp\" STRING,event_time TIMESTAMP(3) TIME INDEX,PRIMARY KEY(tenant)) " + logPartitions + " ENGINE=mito")

	request("POST", greptime+"/v1/pipelines/telemetry?db=public", "application/x-yaml", []byte(logPipeline))

}
func awaitMetrics(address string, f fixture) {
	query := fmt.Sprintf("sum(count_over_time(fleet_load[%ds]))", f.Points)
	end := time.Now().Add(90 * time.Second)
	for time.Now().Before(end) {
		b := request("GET", address+"/api/v1/query?"+url.Values{"query": {query}, "time": {strconv.FormatFloat(float64(f.Start+int64(f.Points-1)*1000)/1000, 'f', 3, 64)}}.Encode(), "", nil)
		var v struct {
			Status string `json:"status"`
			Data   struct {
				Result []struct {
					Value []any `json:"value"`
				} `json:"result"`
			} `json:"data"`
		}
		check(json.Unmarshal(b, &v))
		if v.Status == "success" && len(v.Data.Result) == 1 {
			n, e := strconv.ParseFloat(fmt.Sprint(v.Data.Result[0].Value[1]), 64)
			check(e)
			if n == float64(f.Series*f.Points) {
				return
			}
		}
		time.Sleep(time.Second)
	}
	panic("metrics visibility timeout: " + address)
}
func verify(f fixture) {
	// Read every metric sample from Greptime SQL and VictoriaMetrics' native export.
	seen := make([]bool, f.Series*f.Points)
	for first := 0; first < f.Series; first += 500 {
		rows := sql(fmt.Sprintf("SELECT host,tenant,CAST(greptime_timestamp AS BIGINT),greptime_value FROM fleet_load WHERE host >= '%s' AND host < '%s'", host(first), host(min(first+500, f.Series))))
		for _, r := range rows {
			h, e := strconv.Atoi(strings.TrimPrefix(r[0].(string), "h"))
			check(e)
			ts := int64(r[2].(float64))
			if (ts-f.Start)%1000 != 0 {
				panic("misaligned metric timestamp")
			}
			point := int((ts - f.Start) / 1000)
			idx := h*f.Points + point
			if h < 0 || h >= f.Series || point < 0 || point >= f.Points || seen[idx] || r[1] != strconv.Itoa(h%100) || r[3] != value(h, point) {
				panic(fmt.Sprintf("invalid Greptime metric: %v", r))
			}
			seen[idx] = true
		}
	}
	complete(seen, "Greptime metrics")
	awaitMetrics(env("VM_QUERY", "http://vmselect:8481/select/0/prometheus"), f)
	b := request("GET", env("VM_QUERY", "http://vmselect:8481/select/0/prometheus")+"/api/v1/export?match[]=fleet_load&start="+strconv.FormatInt(f.Start/1000, 10)+"&end="+strconv.FormatInt(f.Start/1000+int64(f.Points), 10), "", nil)
	seen = make([]bool, f.Series*f.Points)
	scan := bufio.NewScanner(bytes.NewReader(b))
	scan.Buffer(make([]byte, 65536), 16*1024*1024)
	for scan.Scan() {
		var r struct {
			Metric     map[string]string `json:"metric"`
			Values     []float64         `json:"values"`
			Timestamps []int64           `json:"timestamps"`
		}
		check(json.Unmarshal(scan.Bytes(), &r))
		h, e := strconv.Atoi(strings.TrimPrefix(r.Metric["host"], "h"))
		check(e)
		if len(r.Values) != len(r.Timestamps) {
			panic("mismatched export arrays")
		}
		for j, v := range r.Values {
			p := int((r.Timestamps[j] - f.Start) / 1000)
			idx := h*f.Points + p
			if h < 0 || h >= f.Series || p < 0 || p >= f.Points || seen[idx] || r.Metric["tenant"] != strconv.Itoa(h%100) || v != value(h, p) {
				panic("invalid VictoriaMetrics sample")
			}
			seen[idx] = true
		}
	}
	check(scan.Err())
	complete(seen, "VictoriaMetrics samples")
	// Full event bodies are verified independently of counts/sums.
	seen = make([]bool, f.Logs)
	for first := 0; first < f.Logs; first += 10000 {
		rows := sql(fmt.Sprintf("SELECT event_id,tenant,level,message,timestamp,CAST(event_time AS BIGINT) FROM events WHERE event_id >= %d AND event_id < %d", first, min(first+10000, f.Logs)))
		for _, r := range rows {
			i := int(r[0].(float64))
			if i < 0 || i >= f.Logs || seen[i] {
				panic("invalid/duplicate Greptime event")
			}
			want := logRow(f, i)
			if r[1] != want["tenant"] || r[2] != want["level"] || r[3] != want["message"] || r[4] != want["timestamp"] || int64(r[5].(float64)) != f.Start+int64(i) {
				panic("Greptime event body mismatch")
			}
			seen[i] = true
		}
	}
	complete(seen, "Greptime logs")
	seen = make([]bool, f.Logs)
	for first := 0; first < f.Logs; first += 10000 {
		last := min(first+10000, f.Logs)
		filter := "_time:[" + time.UnixMilli(f.Start+int64(first)).UTC().Format(time.RFC3339Nano) + ", " + time.UnixMilli(f.Start+int64(last)).UTC().Format(time.RFC3339Nano) + ")"
		body := request("POST", env("VLOGS", "http://vlogs:9428")+"/select/logsql/query", "application/x-www-form-urlencoded", []byte(url.Values{"query": {filter}, "limit": {"10001"}}.Encode()))
		scan := bufio.NewScanner(bytes.NewReader(body))
		for scan.Scan() {
			var r map[string]string
			check(json.Unmarshal(scan.Bytes(), &r))
			i, e := strconv.Atoi(r["event_id"])
			check(e)
			if i < 0 || i >= f.Logs || seen[i] {
				panic("invalid/duplicate VictoriaLogs event")
			}
			want := logRow(f, i)
			if r["tenant"] != want["tenant"] || r["level"] != want["level"] || r["_msg"] != want["message"] {
				panic("VictoriaLogs body mismatch")
			}
			actualTime, e := time.Parse(time.RFC3339Nano, r["_time"])
			check(e)
			if actualTime.UnixMilli() != f.Start+int64(i) {
				panic("VictoriaLogs timestamp mismatch")
			}
			seen[i] = true
		}
		check(scan.Err())

	}
	complete(seen, "VictoriaLogs events")
	verifyRecoveryHistory()
	save("correctness", map[string]any{"metric_samples_per_system": f.Series * f.Points, "logs_per_system": f.Logs, "missing": 0, "duplicates": 0, "wrong_values": 0})
	if os.Getenv("STANDALONE") != "true" {
		save("region-placement", sql("SELECT * FROM information_schema.region_peers"))
	}
}
func complete(seen []bool, name string) {
	for i, v := range seen {
		if !v {
			panic(fmt.Sprintf("%s missing %d", name, i))
		}
	}
	fmt.Println(name, len(seen), "verified")
}
func validateQuery(body []byte, q string, f fixture) {
	var v struct {
		Status string `json:"status"`
		Data   struct {
			Result []struct {
				Metric map[string]string `json:"metric"`
				Value  []any             `json:"value"`
			} `json:"result"`
		} `json:"data"`
	}
	check(json.Unmarshal(body, &v))
	if v.Status != "success" {
		panic(string(body))
	}
	want := map[string]float64{}
	for h := 0; h < f.Series; h++ {
		key := "all"
		if strings.Contains(q, "by (tenant)") {
			key = strconv.Itoa(h % 100)
		}
		if strings.HasPrefix(q, "max_over_time") {
			key = host(h)
		}
		want[key] += value(h, f.Points-1)
	}
	if len(v.Data.Result) != len(want) {
		panic("query result cardinality mismatch")
	}
	for _, r := range v.Data.Result {
		key := "all"
		if strings.Contains(q, "by (tenant)") {
			key = r.Metric["tenant"]
		}
		if strings.HasPrefix(q, "max_over_time") {
			key = r.Metric["host"]
		}
		actual, e := strconv.ParseFloat(fmt.Sprint(r.Value[1]), 64)
		check(e)
		expected, ok := want[key]
		if !ok || actual != expected {
			panic(fmt.Sprintf("query %s key %s: expected %g got %g", q, key, expected, actual))
		}
		delete(want, key)
	}
	if len(want) != 0 {
		panic("missing query results")
	}
}

func benchmark(f fixture) {
	queries := []string{"sum(fleet_load)", "sum by (tenant) (fleet_load)", "max_over_time(fleet_load[60s])"}
	out := map[string]any{}
	for _, system := range []string{"greptime", "victoria"} {
		address := env("VM_QUERY", "http://vmselect:8481/select/0/prometheus")
		if system == "greptime" {
			address = greptime + "/v1/prometheus"
		}
		for _, q := range queries {
			var lat []float64
			var total time.Duration
			for i := 0; i < 21; i++ {
				start := time.Now()
				body := request("GET", address+"/api/v1/query?"+url.Values{"query": {q}, "time": {strconv.FormatInt((f.Start/1000)+int64(f.Points-1), 10)}}.Encode(), "", nil)
				validateQuery(body, q, f)
				if i > 0 {
					d := time.Since(start)
					total += d
					lat = append(lat, float64(d)/float64(time.Millisecond))
				}
			}
			out[system+"/"+q] = stats(lat, total, 20, "queries")
		}
	}
	save("query-benchmark", out)
}

// The journal is outside database disks. A failed request remains ambiguous.
func churn() {
	httpClient.Timeout = 10 * time.Second
	sql("CREATE TABLE IF NOT EXISTS recovery_events (event_id BIGINT,customer_id BIGINT,measurement BIGINT,recorded_at TIMESTAMP(3) TIME INDEX,PRIMARY KEY(customer_id,event_id)) PARTITION ON COLUMNS(customer_id) (customer_id < 3334, customer_id >= 3334 AND customer_id < 6667, customer_id >= 6667) ENGINE=mito")
	caseName := env("FAULT_CASE", "process")
	file := filepath.Join(results, "fault-"+caseName+".jsonl")
	log, e := os.OpenFile(file, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(e)
	defer log.Close()
	enc := json.NewEncoder(log)
	record := func(v any) { check(enc.Encode(v)); check(log.Sync()) }
	startID := time.Now().UnixMilli() * 1000
	for batch := 0; batch < 18000; batch++ {
		if _, e := os.Stat(filepath.Join(results, "stop-"+caseName)); e == nil {
			return
		}
		first := startID + int64(batch)*100
		var rows []string
		for j := int64(0); j < 100; j++ {
			id := first + j
			rows = append(rows, fmt.Sprintf("(%d,%d,%d,%d)", id, id%10000, id*3, id/1000))
		}
		record(map[string]any{"stage": "attempt", "first": first, "count": 100, "at": time.Now().UTC()})
		begin := time.Now()
		_, failure := sqlE("INSERT INTO recovery_events VALUES " + strings.Join(rows, ","))
		stage, message := "ack", ""
		if failure != nil {
			stage, message = "ambiguous", failure.Error()
		}
		record(map[string]any{"stage": stage, "first": first, "count": 100, "at": time.Now().UTC(), "error": message, "latency_ms": float64(time.Since(begin)) / float64(time.Millisecond)})
		time.Sleep(100 * time.Millisecond)
	}
	panic("continuous producer expired before recovery")
}

// Observe previously verified baseline samples; new-write visibility is separate.
func watch() {
	httpClient.Timeout = 10 * time.Second
	f := load()
	name := env("FAULT_CASE", "")
	if name == "" || filepath.Base(name) != name {
		panic("invalid FAULT_CASE")
	}
	file, err := os.OpenFile(filepath.Join(results, "reads-"+name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	check(err)
	defer file.Close()
	for sequence := 0; sequence < 18000; sequence++ {
		if _, err := os.Stat(filepath.Join(results, "stop-"+name)); err == nil {
			return
		} else if !os.IsNotExist(err) {
			check(err)
		}
		h := (sequence * 7919) % f.Series
		p := sequence % f.Points
		started := time.Now().UTC()
		outcome, message := "ok", ""
		data, err := sqlE(fmt.Sprintf("SELECT greptime_value FROM fleet_load WHERE host='%s' AND tenant='%d' AND greptime_timestamp=%d LIMIT 2", host(h), h%100, f.Start+int64(p)*1000))
		if err != nil {
			outcome, message = "error", err.Error()
		} else if len(data) == 0 {
			outcome = "missing"
		} else if len(data) != 1 || len(data[0]) != 1 || data[0][0] != value(h, p) {
			outcome = "wrong"
		}
		check(json.NewEncoder(file).Encode(map[string]any{"started_at": started, "at": time.Now().UTC(), "outcome": outcome, "host": host(h), "point": p, "latency_ms": float64(time.Since(started)) / float64(time.Millisecond), "error": message}))
		check(file.Sync())
		time.Sleep(100 * time.Millisecond)
	}
	panic("reader expired before recovery")
}

func verifyFault() {
	caseName := env("FAULT_CASE", "process")
	f, e := os.Open(filepath.Join(results, "fault-"+caseName+".jsonl"))
	check(e)
	defer f.Close()
	scan := bufio.NewScanner(f)
	acked := map[int64]bool{}
	attempted := map[int64]bool{}
	ambiguous := 0
	for scan.Scan() {
		var r struct {
			Stage string `json:"stage"`
			First int64  `json:"first"`
			Count int64  `json:"count"`
		}
		check(json.Unmarshal(scan.Bytes(), &r))
		for j := int64(0); j < r.Count; j++ {
			if r.Stage == "attempt" {
				attempted[r.First+j] = true
			}
			if r.Stage == "ack" {
				acked[r.First+j] = true
			}
		}
		if r.Stage == "ambiguous" {
			ambiguous++
		}
	}
	check(scan.Err())
	if len(acked) == 0 {
		panic("no acknowledged fault writes")
	}
	rows := sql("SELECT event_id,customer_id,measurement FROM recovery_events")
	seen := map[int64]bool{}
	for _, r := range rows {
		id := int64(r[0].(float64))
		if !attempted[id] {
			continue
		}
		if seen[id] || int64(r[1].(float64)) != id%10000 || int64(r[2].(float64)) != id*3 {
			panic("corrupt or duplicate fault row")
		}
		seen[id] = true
	}
	for id := range acked {
		if !seen[id] {
			panic(fmt.Sprintf("lost acknowledged row %d", id))
		}
	}
	save("fault-"+caseName+"-reconciliation", map[string]any{"acknowledged": len(acked), "observed_attempted": len(seen), "ambiguous_batches": ambiguous, "missing_acknowledged": 0, "duplicates": 0, "wrong_values": 0})
}

func drop() {
	for _, table := range []string{"fleet_load", "events", "recovery_events", "greptime_physical_table"} {
		sql("DROP TABLE IF EXISTS " + table)
	}
	for versions := 0; versions < 100; versions++ {
		req, e := http.NewRequest("GET", greptime+"/v1/pipelines/telemetry?db=public", nil)
		check(e)
		response, e := httpClient.Do(req)
		check(e)
		body, e := io.ReadAll(response.Body)
		check(e)
		check(response.Body.Close())
		if response.StatusCode == 404 {
			break
		}
		if response.StatusCode == 400 {
			var missing struct {
				Error string `json:"error"`
			}
			check(json.Unmarshal(body, &missing))
			if missing.Error == "Failed to load pipeline into cache: Pipeline not found, name: telemetry, version: latest" {
				break
			}
		}
		if response.StatusCode != 200 {
			panic(string(body))
		}
		var pipelines struct {
			Pipelines []struct {
				Version string `json:"version"`
			} `json:"pipelines"`
		}
		check(json.Unmarshal(body, &pipelines))
		if len(pipelines.Pipelines) == 0 {
			break
		}
		for _, pipeline := range pipelines.Pipelines {
			if pipeline.Version == "" {
				panic("missing native pipeline version")
			}
			request("DELETE", greptime+"/v1/pipelines/telemetry?"+url.Values{"db": {"public"}, "version": {pipeline.Version}}.Encode(), "", nil)
		}
		if versions == 99 {
			panic("pipeline cleanup did not converge")
		}
	}
	address := strings.Replace(env("VM_QUERY", "http://vmselect:8481/select/0/prometheus"), "/select/", "/delete/", 1)
	request("POST", address+"/api/v1/admin/tsdb/delete_series", "application/x-www-form-urlencoded", []byte("match%5B%5D=fleet_load"))
	logs := env("VLOGS", "http://vlogs:9428")
	var task struct {
		ID string `json:"task_id"`
	}
	check(json.Unmarshal(request("POST", logs+"/delete/run_task?filter=*", "", nil), &task))
	if task.ID == "" {
		panic("missing log deletion task ID")
	}
	end := time.Now().Add(300 * time.Second)
	for time.Now().Before(end) {
		var active []struct {
			ID string `json:"task_id"`
		}
		check(json.Unmarshal(request("GET", logs+"/delete/active_tasks", "", nil), &active))
		found := false
		for _, t := range active {
			if t.ID == task.ID {
				found = true
			}
		}
		if !found {
			save("cleanup", map[string]any{"greptime_tables_dropped": true, "victoria_series_deleted": true, "victorialogs_task_completed": task.ID})
			return
		}
		time.Sleep(time.Second)
	}
	panic("log deletion timeout")
}

func main() {
	if len(os.Args) == 3 && os.Args[1] == "sql" {
		check(json.NewEncoder(os.Stdout).Encode(sql(os.Args[2])))
		return
	}
	if len(os.Args) == 3 && os.Args[1] == "health" {
		request("GET", os.Args[2], "", nil)
		return
	}
	if len(os.Args) != 2 {
		panic("usage: client seed|verify|benchmark|drop")
	}
	check(os.MkdirAll(results, 0755))
	switch os.Args[1] {
	case "churn":
		churn()
	case "watch":
		watch()
	case "verify-fault":
		verifyFault()
		verifyRecoveryHistory()
	case "seed":
		if _, e := os.Stat(filepath.Join(results, "fixture.json")); !errors.Is(e, os.ErrNotExist) {
			tables := sql("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('fleet_load','events')")
			if len(tables) != 0 {
				panic("existing workload: use make down before reseeding")
			}
			archive := filepath.Join(results, "archive-"+time.Now().UTC().Format("20060102T150405.000000000"))
			check(os.Mkdir(archive, 0755))
			entries, e := os.ReadDir(results)
			check(e)
			for _, entry := range entries {
				if !entry.IsDir() {
					check(os.Rename(filepath.Join(results, entry.Name()), filepath.Join(archive, entry.Name())))
				}
			}
		}
		f := fixture{number("SERIES", 10000), number("POINTS", 100), number("LOGS", 1000000), time.Now().Add(-time.Hour).Truncate(time.Second).UnixMilli()}
		save("fixture", f)
		setup(f)
		out := map[string]any{}
		for _, system := range []string{"greptime", "victoria"} {
			out[system+"/metrics"] = writeMetrics(system, f)
			out[system+"/logs"] = writeLogs(system, f)
		}
		save("ingest-benchmark", out)
	case "verify":
		verify(load())
	case "benchmark":
		benchmark(load())
	case "drop":
		drop()
	default:
		panic("unknown action")
	}
}
