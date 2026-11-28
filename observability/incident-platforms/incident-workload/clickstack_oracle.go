package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

func nativeAttributes(row nativeRow, key string) (map[string]string, error) {
	var values map[string]string
	if err := json.Unmarshal(row[key], &values); err != nil || values == nil {
		return nil, fmt.Errorf("invalid native attribute map %s", key)
	}
	return values, nil
}

func requireAttributes(row nativeRow, key string, expected map[string]string) error {
	actual, err := nativeAttributes(row, key)
	if err != nil {
		return err
	}
	for name, value := range expected {
		if actual[name] != value {
			return fmt.Errorf("%s[%s]=%q expected=%q", key, name, actual[name], value)
		}
	}
	return nil
}

func checkClickStackCommon(row nativeRow, service string) error {
	for key, expected := range map[string]string{"ServiceName": service, "ScopeName": "checkout-incident-v1", "ScopeVersion": "1"} {
		if err := wantString(row, key, expected); err != nil {
			return err
		}
	}
	return requireAttributes(row, "ResourceAttributes", map[string]string{"service.name": service, "deployment.environment.name": "local-lab"})
}

func checkClickStackLog(f Fixture, row nativeRow) (int, error) {
	attrs, err := nativeAttributes(row, "SourceAttributes")
	if err != nil {
		return 0, err
	}
	id, err := strconv.Atoi(attrs["log.id"])
	if err != nil || id < 0 || id >= f.Requests*f.LogsPerRequest {
		return 0, errors.New("invalid native log identity")
	}
	body := f.body(id)
	raw, err := json.Marshal(body)
	if err != nil {
		return 0, err
	}
	role, severity, number := 0, "info", int64(9)
	if incident(body.Request) {
		role, severity, number = 2, "error", 17
	}
	for key, value := range map[string]string{"Body": string(raw), "TraceId": body.TraceID, "SpanId": hex.EncodeToString(f.spanID(body.Request, role)), "SeverityText": severity} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	for key, value := range map[string]int64{"StampNS": int64(f.logStamp(id)), "TraceFlags": 1, "SeverityNumber": number} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	if err = requireAttributes(row, "SourceAttributes", map[string]string{"run": f.Run, "tenant": body.Tenant, "log.id": strconv.Itoa(id)}); err != nil {
		return 0, err
	}
	return id, checkClickStackCommon(row, "checkout")
}

func checkClickStackMetric(f Fixture, row nativeRow) (int, error) {
	attrs, err := nativeAttributes(row, "Attributes")
	if err != nil {
		return 0, err
	}
	series, err := strconv.Atoi(attrs["series"])
	stamp, stampErr := integerField(row, "StampS")
	point := stamp - f.StartMS/1000
	if err != nil || stampErr != nil || series < 0 || series >= f.Requests || point < 0 || point >= int64(f.MetricPoints) || f.StartMS%1000 != 0 {
		return 0, errors.New("native gauge identity or whole-second time differs")
	}
	id := int(point)*f.Requests + series
	_, _, expected := f.metricValue(id)
	value, err := strconv.ParseFloat(string(row["Value"]), 64)
	if err != nil || value != expected {
		return 0, fmt.Errorf("native gauge value differs: %s expected=%f", row["Value"], expected)
	}
	for key, value := range map[string]string{"MetricName": "checkout_latency_microseconds", "MetricUnit": "us"} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	if err = requireAttributes(row, "Attributes", map[string]string{"run": f.Run, "tenant": tenant(series), "series": strconv.Itoa(series)}); err != nil {
		return 0, err
	}
	for key, value := range map[string]int64{"Flags": 0, "StartS": 0} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	return id, checkClickStackCommon(row, "checkout")
}

func checkClickStackTrace(f Fixture, row nativeRow) (int, error) {
	attrs, err := nativeAttributes(row, "SpanAttributes")
	if err != nil {
		return 0, err
	}
	request, err := strconv.Atoi(attrs["request.id"])
	if err != nil || request < 0 || request >= f.Requests {
		return 0, errors.New("invalid native trace request")
	}
	name, err := stringField(row, "SpanName")
	if err != nil {
		return 0, err
	}
	role, ok := map[string]int{"checkout": 0, "inventory.reserve": 1, "payment.authorize": 2}[name]
	if !ok {
		return 0, errors.New("unexpected native span name")
	}
	expected := f.span(request, role)
	kind, status := "Client", "Ok"
	if role == 0 {
		kind = "Server"
	}
	if expected.Status.Code == tracepb.Status_STATUS_CODE_ERROR {
		status = "Error"
	}
	for key, value := range map[string]string{"TraceId": hex.EncodeToString(expected.TraceId), "SpanId": hex.EncodeToString(expected.SpanId), "ParentSpanId": hex.EncodeToString(expected.ParentSpanId), "TraceState": expected.TraceState, "SpanName": expected.Name, "SpanKind": kind, "StatusCode": status, "StatusMessage": expected.Status.Message} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	for key, value := range map[string]int64{"StampNS": int64(expected.StartTimeUnixNano), "Duration": int64(expected.EndTimeUnixNano - expected.StartTimeUnixNano)} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	if err = requireAttributes(row, "SpanAttributes", map[string]string{"run": f.Run, "tenant": tenant(request), "request.id": strconv.Itoa(request), "order.amount_cents": strconv.Itoa(100 + request%100000)}); err != nil {
		return 0, err
	}
	var names []string
	var stamps []int64
	var events []map[string]string
	if json.Unmarshal(row["Events.Name"], &names) != nil || json.Unmarshal(row["EventNanos"], &stamps) != nil || json.Unmarshal(row["Events.Attributes"], &events) != nil || len(names) != 1 || len(stamps) != 1 || len(events) != 1 || names[0] != "operation.started" || stamps[0] != int64(expected.Events[0].TimeUnixNano) || events[0]["request.id"] != strconv.Itoa(request) {
		return 0, errors.New("native span event differs")
	}
	var links, linkSpans, states []string
	var linkAttrs []map[string]string
	if json.Unmarshal(row["Links.TraceId"], &links) != nil || json.Unmarshal(row["Links.SpanId"], &linkSpans) != nil || json.Unmarshal(row["Links.TraceState"], &states) != nil || json.Unmarshal(row["Links.Attributes"], &linkAttrs) != nil || len(links) != len(expected.Links) || len(linkSpans) != len(links) || len(states) != len(links) || len(linkAttrs) != len(links) {
		return 0, errors.New("native span link shape differs")
	}
	for index, link := range expected.Links {
		if links[index] != hex.EncodeToString(link.TraceId) || linkSpans[index] != hex.EncodeToString(link.SpanId) || states[index] != link.TraceState || linkAttrs[index]["relationship"] != "previous-checkout" {
			return 0, errors.New("native span link value differs")
		}
	}
	return request*3 + role, checkClickStackCommon(row, []string{"checkout", "inventory", "payment"}[role])
}

func queryClickStackRange(ctx context.Context, f Fixture, signal string, first, last int) ([]nativeRow, error) {
	table, attr := "otel_logs", "LogAttributes"
	fields := "Body,TraceId,SpanId,TraceFlags,SeverityText,SeverityNumber,ServiceName,ResourceAttributes,ScopeName,ScopeVersion,toUnixTimestamp64Nano(Timestamp) AS StampNS,mapFilter((k,v)->k IN ('run','tenant','log.id'),LogAttributes) AS SourceAttributes"
	if signal == "metrics" {
		table, attr = "otel_metrics_gauge", "Attributes"
		fields = "Attributes,Value,MetricName,MetricUnit,Flags,ServiceName,ResourceAttributes,ScopeName,ScopeVersion,toUnixTimestamp(TimeUnix) AS StampS,toUnixTimestamp(StartTimeUnix) AS StartS"
	} else if signal == "traces" {
		table, attr = "otel_traces", "SpanAttributes"
		fields = "*,toUnixTimestamp64Nano(Timestamp) AS StampNS,arrayMap(x->toUnixTimestamp64Nano(x),`Events.Timestamp`) AS EventNanos"
	} else if signal != "logs" {
		return nil, errors.New("unknown ClickStack signal")
	}
	predicate := fmt.Sprintf("toInt64OrNull(%s['log.id']) >= %d AND toInt64OrNull(%s['log.id']) < %d", attr, first, attr, last)
	if signal == "traces" {
		predicate = fmt.Sprintf("toInt64OrNull(%s['request.id']) >= %d AND toInt64OrNull(%s['request.id']) < %d", attr, first, attr, last)
	} else if signal == "metrics" {
		predicate = fmt.Sprintf("toUnixTimestamp(TimeUnix)=%d AND toInt64OrNull(Attributes['series']) >= %d AND toInt64OrNull(Attributes['series']) < %d", f.StartMS/1000+int64(first/f.Requests), first%f.Requests, (last-1)%f.Requests+1)
	}
	return clickStackQuery(ctx, fmt.Sprintf("SELECT %s FROM %s WHERE %s['run']='%s' AND (%s) LIMIT 10001", fields, table, attr, f.Run, predicate))
}

func readClickStack(ctx context.Context, f Fixture, signal string) ([]int, error) {
	table, attr, factor, total := "otel_logs", "LogAttributes", 1, f.Requests*f.LogsPerRequest
	if signal == "metrics" {
		table, attr, total = "otel_metrics_gauge", "Attributes", f.Requests*f.MetricPoints
	} else if signal == "traces" {
		table, attr, factor, total = "otel_traces", "SpanAttributes", 3, f.Requests
	} else if signal != "logs" {
		return nil, errors.New("unknown ClickStack signal")
	}
	seen := make([]int, total*factor)
	for first := 0; first < total; {
		last := min(first+1000, total)
		if signal == "metrics" {
			last = min(last, (first/f.Requests+1)*f.Requests)
		}
		rows, err := queryClickStackRange(ctx, f, signal, first, last)
		if err != nil {
			return nil, err
		}
		if len(rows) >= 10001 {
			return nil, errors.New("native row cap reached; completeness unproven")
		}
		for _, row := range rows {
			var id int
			switch signal {
			case "logs":
				id, err = checkClickStackLog(f, row)
			case "metrics":
				id, err = checkClickStackMetric(f, row)
			case "traces":
				id, err = checkClickStackTrace(f, row)
			}
			if err != nil {
				return nil, err
			}
			if id/factor < first || id/factor >= last {
				return nil, errors.New("native row outside requested identity range")
			}
			seen[id]++
		}
		first = last
	}
	rows, err := clickStackQuery(ctx, fmt.Sprintf("SELECT count() AS records FROM %s WHERE %s['run']='%s'", table, attr, f.Run))
	if err != nil {
		return nil, err
	}
	if len(rows) != 1 {
		return nil, errors.New("native global inventory did not return one row")
	}
	count, err := integerField(rows[0], "records")
	var found int64
	for _, copies := range seen {
		found += int64(copies)
	}
	if err != nil || count != found {
		return nil, fmt.Errorf("native run inventory=%d, decoded rows=%d; unseen or out-of-range data", count, found)
	}
	return seen, nil
}

func verifyClickStack(ctx context.Context, f Fixture, directory, report string) error {
	return verifyNative(ctx, f, directory, report, "clickstack", strings.Join([]string{"Native logical projection, result cache disabled", "integer attributes are strings; log severity text is lowercased by the native processor", "gauge schema uses whole-second DateTime; subsecond gauge precision is not qualified", "main-span/link flags and log observed-time are absent from this native schema; not lossless OTLP"}, "; "), readClickStack)
}

func clickStackPoint(ctx context.Context, f Fixture, kind string, id int) (bool, error) {
	rows, err := queryClickStackRange(ctx, f, kind, id, id+1)
	if err != nil {
		return false, err
	}
	seen := map[int]bool{}
	for _, row := range rows {
		var actual int
		switch kind {
		case "metrics":
			actual, err = checkClickStackMetric(f, row)
		case "logs":
			actual, err = checkClickStackLog(f, row)
		case "traces":
			actual, err = checkClickStackTrace(f, row)
		}
		if err != nil {
			return false, &wrongValue{err}
		}
		logical := actual
		if kind == "traces" {
			logical /= 3
		}
		if logical != id || seen[actual] {
			return false, &wrongValue{errors.New("wrong/duplicate native point identity")}
		}
		seen[actual] = true
	}
	expected := 1
	if kind == "traces" {
		expected = 3
	}
	return len(seen) == expected, nil
}
