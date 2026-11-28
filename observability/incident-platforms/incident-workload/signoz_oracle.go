package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
)

func sigNumber(row nativeRow, key, attribute string) (int, error) {
	var values map[string]float64
	if err := json.Unmarshal(row[key], &values); err != nil {
		return 0, err
	}
	value, ok := values[attribute]
	if !ok || math.Trunc(value) != value || value < 0 || value > 1000000 {
		return 0, fmt.Errorf("invalid native number %s[%s]", key, attribute)
	}
	return int(value), nil
}
func sigResource(row nativeRow, key, service string) error {
	return requireAttributes(row, key, map[string]string{"service.name": service, "deployment.environment.name": "local-lab"})
}
func checkSigLog(f Fixture, row nativeRow) (int, error) {
	id, err := sigNumber(row, "attributes_number", "log.id")
	if err != nil || id >= f.Requests*f.LogsPerRequest {
		return 0, errors.New("invalid native log identity")
	}
	body := f.body(id)
	raw, _ := json.Marshal(body)
	role, severity, number := 0, "INFO", int64(9)
	if incident(body.Request) {
		role, severity, number = 2, "ERROR", 17
	}
	for key, value := range map[string]string{"body": string(raw), "trace_id": body.TraceID, "span_id": hex.EncodeToString(f.spanID(body.Request, role)), "severity_text": severity, "scope_name": "checkout-incident-v1", "scope_version": "1"} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	for key, value := range map[string]int64{"timestamp": int64(f.logStamp(id)), "observed_timestamp": int64(f.logStamp(id)), "trace_flags": 1, "severity_number": number} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	if err = requireAttributes(row, "attributes_string", map[string]string{"run": f.Run, "tenant": body.Tenant}); err != nil {
		return 0, err
	}
	return id, sigResource(row, "resources_string", "checkout")
}
func checkSigTrace(f Fixture, row nativeRow) (int, error) {
	request, err := sigNumber(row, "attributes_number", "request.id")
	if err != nil || request >= f.Requests {
		return 0, errors.New("invalid native span request")
	}
	name, err := stringField(row, "name")
	if err != nil {
		return 0, err
	}
	role, ok := map[string]int{"checkout": 0, "inventory.reserve": 1, "payment.authorize": 2}[name]
	if !ok {
		return 0, errors.New("unexpected native span name")
	}
	expected := f.span(request, role)
	for key, value := range map[string]string{"trace_id": hex.EncodeToString(expected.TraceId), "span_id": hex.EncodeToString(expected.SpanId), "parent_span_id": hex.EncodeToString(expected.ParentSpanId), "trace_state": expected.TraceState, "name": expected.Name, "status_message": expected.Status.Message} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	for key, value := range map[string]int64{"StampNS": int64(expected.StartTimeUnixNano), "duration_nano": int64(expected.EndTimeUnixNano - expected.StartTimeUnixNano), "kind": int64(expected.Kind), "status_code": int64(expected.Status.Code), "flags": int64(expected.Flags)} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	amount, err := sigNumber(row, "attributes_number", "order.amount_cents")
	if err != nil || amount != 100+request%100000 {
		return 0, errors.New("native order amount differs")
	}
	if err = requireAttributes(row, "attributes_string", map[string]string{"run": f.Run, "tenant": tenant(request)}); err != nil {
		return 0, err
	}
	var scope struct {
		Name    string `json:"name"`
		Version string `json:"version"`
	}
	if json.Unmarshal(row["scope"], &scope) != nil || scope.Name != "checkout-incident-v1" || scope.Version != "1" {
		return 0, errors.New("native trace scope differs")
	}
	var events []string
	if json.Unmarshal(row["events"], &events) != nil || len(events) != 1 {
		return 0, errors.New("native event shape differs")
	}
	var event struct {
		Name       string            `json:"name"`
		Stamp      uint64            `json:"timeUnixNano"`
		Attributes map[string]string `json:"attributeMap"`
	}
	if json.Unmarshal([]byte(events[0]), &event) != nil || event.Name != "operation.started" || event.Stamp != expected.Events[0].TimeUnixNano || event.Attributes["request.id"] != strconv.Itoa(request) {
		return 0, errors.New("native event value differs")
	}
	linkJSON, err := stringField(row, "links")
	if err != nil {
		return 0, err
	}
	var links []struct {
		TraceID string `json:"traceId"`
		SpanID  string `json:"spanId"`
		Type    string `json:"refType"`
	}
	if json.Unmarshal([]byte(linkJSON), &links) != nil || len(links) != 1+len(expected.Links) {
		return 0, errors.New("native reference shape differs")
	}
	if links[0].TraceID != hex.EncodeToString(expected.TraceId) || links[0].SpanID != hex.EncodeToString(expected.ParentSpanId) || links[0].Type != "CHILD_OF" {
		return 0, errors.New("native parent reference differs")
	}
	for i, link := range expected.Links {
		actual := links[i+1]
		if actual.TraceID != hex.EncodeToString(link.TraceId) || actual.SpanID != hex.EncodeToString(link.SpanId) || actual.Type != "FOLLOWS_FROM" {
			return 0, errors.New("native follows-from reference differs")
		}
	}
	return request*3 + role, sigResource(row, "resources_string", []string{"checkout", "inventory", "payment"}[role])
}

type sigSeries struct {
	series           int
	env, temporality string
}

func sigMetadata(ctx context.Context, f Fixture, wanted ...int) (map[uint64]sigSeries, error) {
	extra := ""
	if len(wanted) > 1 {
		return nil, errors.New("only one native series filter is supported")
	}
	if len(wanted) == 1 {
		if wanted[0] < 0 || wanted[0] >= f.Requests {
			return nil, errors.New("invalid series filter")
		}
		extra = fmt.Sprintf(" AND attrs['series']='%d'", wanted[0])
	}
	// Restarted collectors may serialize equivalent label maps in a different
	// order. Bound each metadata page by logical series, then validate every row.
	var rows []nativeRow
	for first := 0; first < f.Requests; first += 1000 {
		pageFilter := extra
		if len(wanted) == 0 {
			pageFilter = fmt.Sprintf(" AND toUInt64(attrs['series']) >= %d AND toUInt64(attrs['series']) < %d", first, first+1000)
		}
		page, err := nativeClickHouseQuery(ctx, "signoz", fmt.Sprintf("SELECT env,temporality,fingerprint,metric_name,unit,type,labels,attrs,scope_attrs,resource_attrs FROM signoz_metrics.time_series_v4 WHERE metric_name='checkout_latency_microseconds' AND attrs['run']='%s'%s GROUP BY ALL LIMIT 10001", f.Run, pageFilter))
		if err != nil {
			return nil, err
		}
		if len(page) >= 10001 {
			return nil, errors.New("native metadata page cap reached")
		}
		rows = append(rows, page...)
		if len(wanted) == 1 {
			break
		}
	}
	result := map[uint64]sigSeries{}
	for _, row := range rows {
		attrs, err := nativeAttributes(row, "attrs")
		if err != nil {
			return nil, err
		}
		series, err := strconv.Atoi(attrs["series"])
		if err != nil || series < 0 || series >= f.Requests {
			return nil, errors.New("native series identity differs")
		}
		if err = requireAttributes(row, "attrs", map[string]string{"run": f.Run, "series": strconv.Itoa(series), "tenant": tenant(series)}); err != nil {
			return nil, err
		}
		for key, value := range map[string]string{"metric_name": "checkout_latency_microseconds", "unit": "us", "type": "Gauge", "temporality": "Unspecified"} {
			if err = wantString(row, key, value); err != nil {
				return nil, err
			}
		}
		if err = sigResource(row, "resource_attrs", "checkout"); err != nil {
			return nil, err
		}
		if err = requireAttributes(row, "scope_attrs", map[string]string{"__scope.name__": "checkout-incident-v1", "__scope.version__": "1"}); err != nil {
			return nil, err
		}
		labelJSON, err := stringField(row, "labels")
		if err != nil {
			return nil, err
		}
		var labels map[string]string
		if json.Unmarshal([]byte(labelJSON), &labels) != nil {
			return nil, errors.New("native labels are not a string map")
		}
		for key, value := range map[string]string{"__name__": "checkout_latency_microseconds", "run": f.Run, "series": strconv.Itoa(series), "tenant": tenant(series), "service.name": "checkout", "deployment.environment.name": "local-lab", "__scope.name__": "checkout-incident-v1", "__scope.version__": "1"} {
			if labels[key] != value {
				return nil, fmt.Errorf("native label %s differs", key)
			}
		}
		fp, err := strconv.ParseUint(string(row["fingerprint"]), 10, 64)
		if err != nil {
			return nil, err
		}
		env, err := stringField(row, "env")
		if err != nil {
			return nil, err
		}
		value := sigSeries{series: series, env: env, temporality: "Unspecified"}
		if old, ok := result[fp]; ok && old != value {
			return nil, errors.New("conflicting native fingerprint metadata")
		}
		result[fp] = value
	}
	return result, nil
}
func readSigMetrics(ctx context.Context, f Fixture) ([]int, error) {
	metadata, err := sigMetadata(ctx, f)
	if err != nil {
		return nil, err
	}
	seen := make([]int, f.Requests*f.MetricPoints)
	filter := fmt.Sprintf("metric_name='checkout_latency_microseconds' AND fingerprint IN (SELECT fingerprint FROM signoz_metrics.time_series_v4 WHERE attrs['run']='%s')", f.Run)
	for point := 0; point < f.MetricPoints; point++ {
		stamp := f.StartMS + int64(point)*1000
		rows, err := nativeClickHouseQuery(ctx, "signoz", fmt.Sprintf("SELECT env,temporality,fingerprint,unix_milli,value,flags FROM signoz_metrics.samples_v4 WHERE %s AND unix_milli=%d LIMIT 10001", filter, stamp))
		if err != nil {
			return nil, err
		}
		if len(rows) >= 10001 {
			return nil, errors.New("native samples cap reached")
		}
		for _, row := range rows {
			fp, err := strconv.ParseUint(string(row["fingerprint"]), 10, 64)
			if err != nil {
				return nil, err
			}
			series, ok := metadata[fp]
			if !ok {
				return nil, errors.New("native sample has no verified metadata")
			}
			for key, value := range map[string]string{"env": series.env, "temporality": series.temporality} {
				if err = wantString(row, key, value); err != nil {
					return nil, err
				}
			}
			if err = wantInteger(row, "unix_milli", stamp); err != nil {
				return nil, err
			}
			if err = wantInteger(row, "flags", 0); err != nil {
				return nil, err
			}
			id := point*f.Requests + series.series
			_, _, expected := f.metricValue(id)
			value, err := strconv.ParseFloat(string(row["value"]), 64)
			if err != nil || value != expected {
				return nil, errors.New("native gauge value differs")
			}
			seen[id]++
		}
	}
	rows, err := nativeClickHouseQuery(ctx, "signoz", "SELECT count() AS records FROM signoz_metrics.samples_v4 WHERE "+filter)
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, errors.New("native metric inventory missing")
	}
	count, err := integerField(rows[0], "records")
	var total int64
	for _, n := range seen {
		total += int64(n)
	}
	if err != nil || count != total {
		return nil, errors.New("native metric inventory contains unseen/out-of-window samples")
	}
	return seen, nil
}
func querySigRange(ctx context.Context, f Fixture, kind string, first, last int) ([]nativeRow, error) {
	table, field, columns := "signoz_logs.logs_v2", "attributes.log.id", "timestamp,observed_timestamp,trace_id,span_id,trace_flags,severity_text,severity_number,body,attributes_string,attributes_number,resources_string,scope_name,scope_version"
	if kind == "traces" {
		table, field, columns = "signoz_traces.signoz_index_v3", "attributes.request.id", "trace_id,span_id,parent_span_id,trace_state,flags,name,kind,duration_nano,status_code,status_message,attributes_string,attributes_number,resources_string,events,links,scope,toUnixTimestamp64Nano(timestamp) AS StampNS"
	} else if kind != "logs" {
		return nil, errors.New("unknown native range kind")
	}
	return nativeClickHouseQuery(ctx, "signoz", fmt.Sprintf("SELECT %s FROM %s WHERE attributes_string['run']='%s' AND %s >= %d AND %s < %d LIMIT 10001", columns, table, f.Run, field, first, field, last))
}
func readSigNoz(ctx context.Context, f Fixture, kind string) ([]int, error) {
	if kind == "metrics" {
		return readSigMetrics(ctx, f)
	}
	total, factor, width, table := f.Requests*f.LogsPerRequest, 1, 5000, "signoz_logs.logs_v2"
	if kind == "traces" {
		total, factor, width, table = f.Requests, 3, 1000, "signoz_traces.signoz_index_v3"
	}
	seen := make([]int, total*factor)
	for first := 0; first < total; first += width {
		last := min(first+width, total)
		rows, err := querySigRange(ctx, f, kind, first, last)
		if err != nil {
			return nil, err
		}
		if len(rows) >= 10001 {
			return nil, errors.New("native range cap reached")
		}
		for _, row := range rows {
			var id int
			if kind == "logs" {
				id, err = checkSigLog(f, row)
			} else {
				id, err = checkSigTrace(f, row)
			}
			if err != nil {
				return nil, err
			}
			if id/factor < first || id/factor >= last {
				return nil, errors.New("native row outside requested range")
			}
			seen[id]++
		}
	}
	rows, err := nativeClickHouseQuery(ctx, "signoz", fmt.Sprintf("SELECT count() AS records FROM %s WHERE attributes_string['run']='%s'", table, f.Run))
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, errors.New("native run inventory missing")
	}
	count, err := integerField(rows[0], "records")
	var found int64
	for _, n := range seen {
		found += int64(n)
	}
	if err != nil || count != found {
		return nil, errors.New("native run inventory contains unseen/out-of-range data")
	}
	return seen, nil
}
func verifySigNoz(ctx context.Context, f Fixture, directory, report string) error {
	return verifyNative(ctx, f, directory, report, "signoz", "Native logical-value projection; millisecond metrics and nanosecond logs/traces; integer event attributes become strings; links become CHILD_OF/FOLLOWS_FROM references without OTLP link attributes/state/flags; not lossless OTLP", readSigNoz)
}

func sigNozPoint(ctx context.Context, f Fixture, kind string, id int) (bool, error) {
	if kind == "metrics" {
		series, stamp, expected := f.metricValue(id)
		metadata, err := sigMetadata(ctx, f, series)
		if err != nil {
			return false, err
		}
		count := 0
		for fp, meta := range metadata {
			rows, err := nativeClickHouseQuery(ctx, "signoz", fmt.Sprintf("SELECT env,temporality,unix_milli,value,flags FROM signoz_metrics.samples_v4 WHERE metric_name='checkout_latency_microseconds' AND fingerprint=%d AND unix_milli=%d LIMIT 2", fp, stamp))
			if err != nil {
				return false, err
			}
			for _, row := range rows {
				value, err := strconv.ParseFloat(string(row["value"]), 64)
				if err != nil || value != expected {
					return false, &wrongValue{errors.New("native metric point differs")}
				}
				for key, want := range map[string]string{"env": meta.env, "temporality": meta.temporality} {
					if err = wantString(row, key, want); err != nil {
						return false, &wrongValue{err}
					}
				}
				if err = wantInteger(row, "unix_milli", stamp); err != nil {
					return false, &wrongValue{err}
				}
				if err = wantInteger(row, "flags", 0); err != nil {
					return false, &wrongValue{err}
				}
				count++
			}
		}
		if count > 1 {
			return false, &wrongValue{errors.New("duplicate native metric point")}
		}
		return count == 1, nil
	}
	rows, err := querySigRange(ctx, f, kind, id, id+1)
	if err != nil {
		return false, err
	}
	seen := map[int]bool{}
	for _, row := range rows {
		var actual int
		if kind == "logs" {
			actual, err = checkSigLog(f, row)
		} else if kind == "traces" {
			actual, err = checkSigTrace(f, row)
		} else {
			return false, errors.New("unknown native point kind")
		}
		if err != nil {
			return false, &wrongValue{err}
		}
		logical := actual
		if kind == "traces" {
			logical /= 3
		}
		if logical != id || seen[actual] {
			return false, &wrongValue{errors.New("wrong/duplicate native point")}
		}
		seen[actual] = true
	}
	expected := 1
	if kind == "traces" {
		expected = 3
	}
	return len(seen) == expected, nil
}
