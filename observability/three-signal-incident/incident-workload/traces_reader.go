package main

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"time"

	common "go.opentelemetry.io/proto/otlp/common/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
	"google.golang.org/protobuf/proto"
)

type spanEvent struct {
	Time       uint64
	Name       string
	Attributes map[string]any
}
type spanLink struct {
	TraceID, SpanID, TraceState string
	Attributes                  map[string]any
}
type canonicalSpan struct {
	TraceID, SpanID, ParentSpanID, TraceState, Name, Kind, Service, ScopeName, ScopeVersion, Status, StatusMessage string
	Start, End                                                                                                     uint64
	Attributes, ResourceAttributes                                                                                 map[string]any
	Events                                                                                                         []spanEvent
	Links                                                                                                          []spanLink
}

func attributeMap(attributes []*common.KeyValue) (map[string]any, error) {
	result := make(map[string]any)
	for _, attribute := range attributes {
		if attribute == nil || attribute.Value == nil {
			return nil, errors.New("nil trace attribute")
		}
		if _, ok := result[attribute.Key]; ok {
			return nil, errors.New("duplicate trace attribute key")
		}
		switch value := attribute.Value.Value.(type) {
		case *common.AnyValue_StringValue:
			result[attribute.Key] = value.StringValue
		case *common.AnyValue_IntValue:
			result[attribute.Key] = value.IntValue
		default:
			return nil, fmt.Errorf("attribute %q has a type outside this fixture", attribute.Key)
		}
	}
	return result, nil
}

func normalizeSpan(span *tracepb.Span, resource []*common.KeyValue, scope *common.InstrumentationScope) (canonicalSpan, error) {
	var result canonicalSpan
	if span == nil || scope == nil {
		return result, errors.New("missing span/scope")
	}
	attrs, err := attributeMap(span.Attributes)
	if err != nil {
		return result, err
	}
	resources, err := attributeMap(resource)
	if err != nil {
		return result, err
	}
	service, ok := resources["service.name"].(string)
	if !ok {
		return result, errors.New("missing resource service.name")
	}
	result = canonicalSpan{TraceID: hex.EncodeToString(span.TraceId), SpanID: hex.EncodeToString(span.SpanId), ParentSpanID: hex.EncodeToString(span.ParentSpanId), TraceState: span.TraceState,
		Name: span.Name, Kind: span.Kind.String(), Service: service, ScopeName: scope.Name, ScopeVersion: scope.Version, Status: span.GetStatus().GetCode().String(), StatusMessage: span.GetStatus().GetMessage(),
		Start: span.StartTimeUnixNano, End: span.EndTimeUnixNano, Attributes: attrs, ResourceAttributes: resources, Events: []spanEvent{}, Links: []spanLink{}}
	for _, event := range span.Events {
		attrs, err := attributeMap(event.Attributes)
		if err != nil {
			return result, err
		}
		result.Events = append(result.Events, spanEvent{Time: event.TimeUnixNano, Name: event.Name, Attributes: attrs})
	}
	for _, link := range span.Links {
		attrs, err := attributeMap(link.Attributes)
		if err != nil {
			return result, err
		}
		result.Links = append(result.Links, spanLink{TraceID: hex.EncodeToString(link.TraceId), SpanID: hex.EncodeToString(link.SpanId), TraceState: link.TraceState, Attributes: attrs})
	}
	return result, nil
}

func expectedSpan(f Fixture, id, role int) (canonicalSpan, error) {
	return normalizeSpan(f.span(id, role), f.resource([]string{"checkout", "inventory", "payment"}[role]).Attributes, scope())
}

func nativeTrace(ctx context.Context, backend string, f Fixture, id int) ([]canonicalSpan, error) {
	endpoint := "http://tempo:3200/api/traces/"
	if backend == "victoria" {
		endpoint = "http://vtraces:10428/select/tempo/api/traces/"
	}
	// Both pinned v1 APIs return a bare Trace whose resourceSpans field has
	// the same wire schema as the official OTLP TracesData. No custom codec.
	response, err := get(ctx, endpoint+hex.EncodeToString(f.traceID(id)), "application/protobuf")
	if err != nil {
		var status *httpError
		if errors.As(err, &status) && status.Status == 404 {
			return nil, nil
		}
		return nil, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, (4<<20)+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > 4<<20 {
		return nil, errors.New("one native trace exceeded 4 MiB")
	}
	var trace tracepb.TracesData
	if err = proto.Unmarshal(raw, &trace); err != nil {
		return nil, err
	}
	var spans []canonicalSpan
	for _, resource := range trace.ResourceSpans {
		if resource.Resource == nil {
			return nil, errors.New("trace lacks resource")
		}
		for _, scope := range resource.ScopeSpans {
			for _, span := range scope.Spans {
				item, err := normalizeSpan(span, resource.Resource.Attributes, scope.Scope)
				if err != nil {
					return nil, err
				}
				spans = append(spans, item)
			}
		}
	}
	return spans, nil
}

func greptimeTraceRange(ctx context.Context, f Fixture, first, last int) ([]canonicalSpan, error) {
	start := time.UnixMilli(f.StartMS + int64(first)*10).UTC().Format(time.RFC3339Nano)
	end := time.UnixMilli(f.StartMS + int64(last)*10).UTC().Format(time.RFC3339Nano)
	query := fmt.Sprintf(`SELECT trace_id,span_id,parent_span_id,span_name,span_kind,CAST(timestamp AS BIGINT),CAST(timestamp_end AS BIGINT),span_status_code,span_status_message,span_attributes,resource_attributes,span_events,span_links,scope_name,scope_version,trace_state,service_name FROM checkout_traces_v2 WHERE span_attributes."run"::STRING='%s' AND timestamp>='%s' AND timestamp<'%s'`, f.Run, start, end)
	rows, err := greptimeRows(ctx, query)
	if err != nil {
		return nil, err
	}
	result := make([]canonicalSpan, 0, len(rows))
	for _, row := range rows {
		if len(row) != 17 {
			return nil, errors.New("unexpected trace SQL columns")
		}
		var s canonicalSpan
		destinations := map[int]*string{0: &s.TraceID, 1: &s.SpanID, 2: &s.ParentSpanID, 3: &s.Name, 4: &s.Kind, 7: &s.Status, 8: &s.StatusMessage, 13: &s.ScopeName, 14: &s.ScopeVersion, 15: &s.TraceState, 16: &s.Service}
		for index, target := range destinations {
			if err = json.Unmarshal(row[index], target); err != nil {
				return nil, err
			}
		}
		if err = json.Unmarshal(row[5], &s.Start); err != nil {
			return nil, err
		}
		if err = json.Unmarshal(row[6], &s.End); err != nil {
			return nil, err
		}
		for index, target := range map[int]*map[string]any{9: &s.Attributes, 10: &s.ResourceAttributes} {
			decoder := json.NewDecoder(strings.NewReader(string(row[index])))
			decoder.UseNumber()
			if err = decoder.Decode(target); err != nil {
				return nil, err
			}
			for key, value := range *target {
				if number, ok := value.(json.Number); ok {
					n, err := strconv.ParseInt(string(number), 10, 64)
					if err != nil {
						return nil, errors.New("trace attribute is outside this integer/string fixture")
					}
					(*target)[key] = n
				}
			}
		}
		s.Events, s.Links = []spanEvent{}, []spanLink{}
		var events []struct {
			Time       string         `json:"time"`
			Name       string         `json:"name"`
			Attributes map[string]any `json:"attributes"`
		}
		decoder := json.NewDecoder(strings.NewReader(string(row[11])))
		decoder.UseNumber()
		if err = decoder.Decode(&events); err != nil {
			return nil, err
		}
		for _, event := range events {
			timestamp, err := time.Parse("2006-01-02 15:04:05.999999999-0700", event.Time)
			if err != nil || timestamp.UnixNano() < 0 {
				return nil, fmt.Errorf("native span event timestamp %q: %v", event.Time, err)
			}
			if err = normalizeIntegerAttributes(event.Attributes); err != nil {
				return nil, err
			}
			s.Events = append(s.Events, spanEvent{Time: uint64(timestamp.UnixNano()), Name: event.Name, Attributes: event.Attributes})
		}
		var links []struct {
			TraceID    string         `json:"trace_id"`
			SpanID     string         `json:"span_id"`
			TraceState string         `json:"trace_state"`
			Attributes map[string]any `json:"attributes"`
		}
		decoder = json.NewDecoder(strings.NewReader(string(row[12])))
		decoder.UseNumber()
		if err = decoder.Decode(&links); err != nil {
			return nil, err
		}
		for _, link := range links {
			if err = normalizeIntegerAttributes(link.Attributes); err != nil {
				return nil, err
			}
			s.Links = append(s.Links, spanLink{TraceID: link.TraceID, SpanID: link.SpanID, TraceState: link.TraceState, Attributes: link.Attributes})
		}
		if s.ResourceAttributes["service.name"] != s.Service {
			return nil, errors.New("indexed service differs from trace resource")
		}
		result = append(result, s)
	}
	return result, nil
}

func traceReadback(ctx context.Context, backend string, f Fixture, allowProjection bool, projected *int) ([]int, error) {
	seen := make([]int, f.Requests*3)
	bySpan := make(map[string]int, len(seen))
	for id := 0; id < f.Requests; id++ {
		for role := 0; role < 3; role++ {
			bySpan[hex.EncodeToString(f.spanID(id, role))] = id*3 + role
		}
	}
	check := func(spans []canonicalSpan) error {
		for _, span := range spans {
			index, ok := bySpan[span.SpanID]
			if !ok {
				return fmt.Errorf("unexpected span id=%s", span.SpanID)
			}
			expected, err := expectedSpan(f, index/3, index%3)
			if err != nil {
				return err
			}
			if !reflect.DeepEqual(span, expected) && allowProjection && reflect.DeepEqual(span, stringAttributeProjection(expected)) {
				*projected++
			} else if !reflect.DeepEqual(span, expected) {
				actual, _ := json.Marshal(span)
				wanted, _ := json.Marshal(expected)
				return fmt.Errorf("span differs id=%s actual=%s expected=%s", span.SpanID, actual, wanted)
			}
			seen[index]++
		}
		return nil
	}
	if backend == "greptime" {
		for first := 0; first < f.Requests; first += 250 {
			spans, err := greptimeTraceRange(ctx, f, first, min(first+250, f.Requests))
			if err != nil {
				return nil, err
			}
			if err = check(spans); err != nil {
				return nil, err
			}
		}
		return seen, nil
	}
	ctx, cancel := context.WithCancel(ctx)
	defer cancel()
	jobs := make(chan int)
	type result struct {
		spans []canonicalSpan
		err   error
	}
	results := make(chan result, 4)
	var workers sync.WaitGroup
	for worker := 0; worker < 4; worker++ {
		workers.Go(func() {
			for id := range jobs {
				spans, err := nativeTrace(ctx, backend, f, id)
				select {
				case results <- result{spans, err}:
				case <-ctx.Done():
					return
				}
			}
		})
	}
	go func() {
		defer close(jobs)
		for id := 0; id < f.Requests; id++ {
			select {
			case jobs <- id:
			case <-ctx.Done():
				return
			}
		}
	}()
	go func() { workers.Wait(); close(results) }()
	var failure error
	for result := range results {
		if failure != nil {
			continue
		}
		if result.err != nil {
			failure = result.err
		} else {
			failure = check(result.spans)
		}
		if failure != nil {
			cancel()
		}
	}
	return seen, failure
}

func normalizeIntegerAttributes(values map[string]any) error {
	for key, value := range values {
		if number, ok := value.(json.Number); ok {
			n, err := strconv.ParseInt(string(number), 10, 64)
			if err != nil {
				return errors.New("attribute outside this integer/string fixture")
			}
			values[key] = n
		}
	}
	return nil
}

// This is an explicit SECONDARY diagnostic, never the strict type oracle.
// Victoria's experimental Tempo compatibility path stringifies numeric attributes.
// Keep its failed exact-type result; this checks whether values/structure survived.
func stringAttributeProjection(source canonicalSpan) canonicalSpan {
	convert := func(values map[string]any) map[string]any {
		result := make(map[string]any, len(values))
		for key, value := range values {
			if number, ok := value.(int64); ok {
				result[key] = strconv.FormatInt(number, 10)
			} else {
				result[key] = value
			}
		}
		return result
	}
	result := source
	result.Attributes = convert(source.Attributes)
	result.ResourceAttributes = convert(source.ResourceAttributes)
	result.Events = make([]spanEvent, len(source.Events))
	for id, event := range source.Events {
		result.Events[id] = event
		result.Events[id].Attributes = convert(event.Attributes)
	}
	result.Links = make([]spanLink, len(source.Links))
	for id, link := range source.Links {
		result.Links[id] = link
		result.Links[id].Attributes = convert(link.Attributes)
	}
	return result
}
