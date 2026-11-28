package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"

	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

func searchOpenObserve(ctx context.Context, f Fixture, kind, stream, predicate string) ([]nativeRow, error) {
	payload := map[string]any{"use_cache": false, "query": map[string]any{
		"sql":        fmt.Sprintf(`SELECT * FROM "%s" WHERE run='%s' AND (%s) LIMIT 10001`, stream, f.Run, predicate),
		"start_time": f.StartMS*1000 - 1, "end_time": (f.StartMS + int64(f.Requests)*10 + 100000) * 1000,
		"from": 0, "size": 10001,
	}}
	raw, err := openObserveJSON(ctx, "POST", "/api/default/_search?type="+kind, payload)
	if err != nil {
		return nil, err
	}
	var response struct {
		Hits             []nativeRow `json:"hits"`
		Partial          bool        `json:"is_partial"`
		ResultCacheRatio float64     `json:"result_cache_ratio"`
	}
	if err = json.Unmarshal(raw, &response); err != nil {
		return nil, err
	}
	if response.Partial || response.ResultCacheRatio != 0 {
		return nil, errors.New("independent oracle received partial or result-cached rows")
	}
	if response.Hits == nil {
		return nil, errors.New("native search omitted its hits array")
	}
	return response.Hits, nil
}

func checkOpenObserveLog(f Fixture, row nativeRow) (int, error) {
	id64, err := integerField(row, "log_id")
	if err != nil || id64 < 0 || id64 >= int64(f.Requests*f.LogsPerRequest) {
		return 0, errors.New("invalid native log id")
	}
	id := int(id64)
	expected := f.body(id)
	body, err := json.Marshal(expected)
	if err != nil {
		return 0, err
	}
	role, severity, number := 0, "INFO", int64(9)
	if incident(expected.Request) {
		role, severity, number = 2, "ERROR", 17
	}
	for key, value := range map[string]string{
		"body": string(body), "run": f.Run, "tenant": expected.Tenant,
		"trace_id": expected.TraceID, "span_id": hex.EncodeToString(f.spanID(expected.Request, role)),
		"severity": severity, "service_name": "checkout", "deployment_environment_name": "local-lab",
		"instrumentation_library_name": "checkout-incident-v1", "instrumentation_library_version": "1",
	} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	if err = wantInteger(row, "_timestamp", int64(f.logStamp(id)/1000)); err != nil {
		return 0, err
	}
	if err = wantInteger(row, "severity_number", number); err != nil {
		return 0, err
	}
	if err = wantInteger(row, "dropped_attributes_count", 0); err != nil {
		return 0, err
	}
	return id, nil
}

func checkOpenObserveMetric(f Fixture, row nativeRow) (int, error) {
	seriesText, err := stringField(row, "series")
	if err != nil {
		return 0, err
	}
	series, err := strconv.Atoi(seriesText)
	stamp, stampErr := integerField(row, "_timestamp")
	point := (stamp - f.StartMS*1000) / 1000000
	if err != nil || stampErr != nil || series < 0 || series >= f.Requests || point < 0 || point >= int64(f.MetricPoints) || (stamp-f.StartMS*1000)%1000000 != 0 {
		return 0, errors.New("native metric identity/time differs")
	}
	id := int(point)*f.Requests + series
	_, _, expected := f.metricValue(id)
	actual, err := strconv.ParseFloat(string(row["value"]), 64)
	if err != nil || actual != expected {
		return 0, fmt.Errorf("wrong metric value id=%d got=%s expected=%f", id, row["value"], expected)
	}
	for key, value := range map[string]string{"run": f.Run, "tenant": tenant(series), "__name__": "checkout_latency_microseconds", "service_name": "checkout", "deployment_environment_name": "local-lab", "instrumentation_library_name": "checkout-incident-v1", "instrumentation_library_version": "1"} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	return id, nil
}

func checkOpenObserveTrace(f Fixture, row nativeRow) (int, error) {
	requestText, err := stringField(row, "request_id")
	if err != nil {
		return 0, err
	}
	id, err := strconv.Atoi(requestText)
	if err != nil || id < 0 || id >= f.Requests {
		return 0, errors.New("invalid trace request id")
	}
	spanID, err := stringField(row, "span_id")
	if err != nil {
		return 0, err
	}
	role := -1
	for candidate := 0; candidate < 3; candidate++ {
		if hex.EncodeToString(f.spanID(id, candidate)) == spanID {
			role = candidate
			break
		}
	}
	if role < 0 {
		return 0, errors.New("unknown native span id")
	}
	span := f.span(id, role)
	status := "OK"
	if incident(id) && role != 1 {
		status = "ERROR"
	}
	service := []string{"checkout", "inventory", "payment"}[role]
	for key, value := range map[string]string{"run": f.Run, "trace_id": hex.EncodeToString(span.TraceId), "tenant": tenant(id), "span_kind": strconv.Itoa(int(span.Kind)), "operation_name": span.Name, "span_status": status, "status_message": span.Status.Message, "order_amount_cents": strconv.Itoa(100 + id%100000), "service_name": service, "service_deployment_environment_name": "local-lab"} {
		if err = wantString(row, key, value); err != nil {
			return 0, err
		}
	}
	for key, value := range map[string]int64{"start_time": int64(span.StartTimeUnixNano), "end_time": int64(span.EndTimeUnixNano), "duration": int64((span.EndTimeUnixNano - span.StartTimeUnixNano) / 1000), "_timestamp": int64(span.StartTimeUnixNano / 1000), "status_code": int64(span.Status.Code)} {
		if err = wantInteger(row, key, value); err != nil {
			return 0, err
		}
	}
	if role != 0 {
		for key, value := range map[string]string{"reference_parent_span_id": hex.EncodeToString(span.ParentSpanId), "reference_parent_trace_id": hex.EncodeToString(span.TraceId), "reference_ref_type": "ChildOf"} {
			if err = wantString(row, key, value); err != nil {
				return 0, err
			}
		}
	} else {
		for _, key := range []string{"reference_parent_span_id", "reference_parent_trace_id", "reference_ref_type"} {
			if raw := row[key]; len(raw) > 0 && string(raw) != "null" && string(raw) != `""` {
				return 0, fmt.Errorf("root has unexpected parent: %s", key)
			}
		}
	}
	var events []map[string]json.RawMessage
	eventText, err := stringField(row, "events")
	if err != nil {
		return 0, err
	}
	if err = json.Unmarshal([]byte(eventText), &events); err != nil || len(events) != 1 {
		return 0, errors.New("trace event count/JSON differs")
	}
	if err = wantString(events[0], "name", "operation.started"); err != nil {
		return 0, err
	}
	if err = wantString(events[0], "request.id", strconv.Itoa(id)); err != nil {
		return 0, err
	}
	if err = wantInteger(events[0], "_timestamp", int64(span.Events[0].TimeUnixNano)); err != nil {
		return 0, err
	}
	linkText, err := stringField(row, "links")
	if err != nil {
		return 0, err
	}
	if err = checkOpenObserveLinks(span.Links, linkText); err != nil {
		return 0, err
	}
	return id*3 + role, nil
}

// The native SpanLink serializer uses camelCase context and flattened attributes.
func checkOpenObserveLinks(expected []*tracepb.Span_Link, payload string) error {
	var links []nativeRow
	if err := json.Unmarshal([]byte(payload), &links); err != nil {
		return err
	}
	if len(links) != len(expected) {
		return errors.New("trace link count differs")
	}
	for index, row := range links {
		var context struct {
			TraceID string `json:"traceId"`
			SpanID  string `json:"spanId"`
			Flags   uint32 `json:"traceFlags"`
			State   string `json:"traceState"`
		}
		if err := json.Unmarshal(row["context"], &context); err != nil {
			return err
		}
		link := expected[index]
		if context.TraceID != hex.EncodeToString(link.TraceId) || context.SpanID != hex.EncodeToString(link.SpanId) || context.Flags != link.Flags || context.State != link.TraceState {
			return errors.New("trace link context differs")
		}
		if err := wantInteger(row, "droppedAttributesCount", 0); err != nil {
			return err
		}
		if len(row) != len(link.Attributes)+2 {
			return errors.New("trace link attribute count differs")
		}
		for _, attribute := range link.Attributes {
			if err := wantString(row, attribute.Key, attribute.Value.GetStringValue()); err != nil {
				return err
			}
		}
	}
	return nil
}

func readOpenObserve(ctx context.Context, f Fixture, signal string) ([]int, error) {
	total := f.Requests
	if signal == "logs" {
		total *= f.LogsPerRequest
	}
	if signal == "metrics" {
		total *= f.MetricPoints
	}
	factor := 1
	if signal == "traces" {
		factor = 3
	}
	seen := make([]int, total*factor)
	var scan func(int, int) error
	scan = func(first, last int) error {
		stream, predicate := "default", ""
		switch signal {
		case "logs":
			predicate = fmt.Sprintf("log_id >= %d AND log_id < %d", first, last)
		case "traces":
			predicate = fmt.Sprintf("CAST(request_id AS BIGINT) >= %d AND CAST(request_id AS BIGINT) < %d", first, last)
		case "metrics":
			stream = "checkout_latency_microseconds"
			point := first / f.Requests
			predicate = fmt.Sprintf("_timestamp=%d AND CAST(series AS BIGINT)>=%d AND CAST(series AS BIGINT)<%d", (f.StartMS+int64(point)*1000)*1000, first%f.Requests, (last-1)%f.Requests+1)
		}
		rows, err := searchOpenObserve(ctx, f, signal, stream, predicate)
		if err != nil {
			return err
		}
		if len(rows) >= 10001 {
			if last-first == 1 {
				return errors.New("native cap reached for one logical ID; completeness unproven")
			}
			middle := first + (last-first)/2
			if err = scan(first, middle); err != nil {
				return err
			}
			return scan(middle, last)
		}
		for _, row := range rows {
			var id int
			switch signal {
			case "logs":
				id, err = checkOpenObserveLog(f, row)
			case "metrics":
				id, err = checkOpenObserveMetric(f, row)
			case "traces":
				id, err = checkOpenObserveTrace(f, row)
			}
			if err != nil {
				return err
			}
			logical := id / factor
			if logical < first || logical >= last {
				return fmt.Errorf("row outside requested predicate: %d", id)
			}
			seen[id]++
		}
		return nil
	}
	width := 5000
	if signal == "traces" {
		width = 1000
	}
	for first := 0; first < total; {
		last := min(first+width, total)
		if signal == "metrics" {
			last = min(last, (first/f.Requests+1)*f.Requests)
		}
		if err := scan(first, last); err != nil {
			return nil, err
		}
		first = last
	}
	return seen, nil
}

func verifyOpenObserve(ctx context.Context, f Fixture, directory, report string) error {
	return verifyNative(ctx, f, directory, report, "openobserve", "Uncached native SQL logical-value projection; numeric span/event attributes are strings; main trace state and ordinary trace scope are absent; span flags are not qualified", readOpenObserve)
}
