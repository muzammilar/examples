package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	logs "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	metrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	traces "go.opentelemetry.io/proto/otlp/collector/trace/v1"
	common "go.opentelemetry.io/proto/otlp/common/v1"
	logpb "go.opentelemetry.io/proto/otlp/logs/v1"
	metricpb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
	tracepb "go.opentelemetry.io/proto/otlp/trace/v1"
)

type Fixture struct {
	Run            string `json:"run"`
	PayloadSeed    string `json:"payload_seed"`
	StartMS        int64  `json:"start_ms"`
	Requests       int    `json:"requests"`
	LogsPerRequest int    `json:"logs_per_request"`
	MetricPoints   int    `json:"metric_points"`
}

func text(key, value string) *common.KeyValue {
	return &common.KeyValue{Key: key, Value: &common.AnyValue{Value: &common.AnyValue_StringValue{StringValue: value}}}
}
func integer(key string, value int64) *common.KeyValue {
	return &common.KeyValue{Key: key, Value: &common.AnyValue{Value: &common.AnyValue_IntValue{IntValue: value}}}
}
func tenant(id int) string {
	if id%2 == 0 {
		return "tenant-0"
	}
	return "tenant-" + strconv.Itoa(1+(id/2)%99)
}
func incident(id int) bool { return id >= 4000 && id < 4500 && id%2 == 0 }
func (f Fixture) traceID(id int) []byte {
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s/trace/%d", f.Run, id)))
	return sum[:16]
}
func (f Fixture) spanID(id, role int) []byte {
	sum := sha256.Sum256([]byte(fmt.Sprintf("%s/span/%d/%d", f.Run, id, role)))
	return sum[:8]
}
func (f Fixture) resource(service string) *resourcepb.Resource {
	return &resourcepb.Resource{Attributes: []*common.KeyValue{text("service.name", service), text("deployment.environment.name", "local-lab")}}
}
func scope() *common.InstrumentationScope {
	return &common.InstrumentationScope{Name: "checkout-incident-v1", Version: "1"}
}

func (f Fixture) span(id, role int) *tracepb.Span {
	start := uint64(f.StartMS+int64(id)*10) * 1000000
	offset, duration := uint64(0), uint64(5000000)
	name, kind := "checkout", tracepb.Span_SPAN_KIND_SERVER
	if role == 1 {
		name, kind, offset, duration = "inventory.reserve", tracepb.Span_SPAN_KIND_CLIENT, 1000000, 2000000
	}
	if role == 2 {
		name, kind, offset, duration = "payment.authorize", tracepb.Span_SPAN_KIND_CLIENT, 3000000, 2000000
	}
	failed := incident(id) && role != 1
	status := &tracepb.Status{Code: tracepb.Status_STATUS_CODE_OK}
	if failed {
		duration = 50000000
		if role == 2 {
			duration = 47000000
		}
		status = &tracepb.Status{Code: tracepb.Status_STATUS_CODE_ERROR, Message: "synthetic payment timeout"}
	}
	attrs := []*common.KeyValue{text("run", f.Run), text("tenant", tenant(id)), integer("request.id", int64(id)), integer("order.amount_cents", int64(100+id%100000))}
	result := &tracepb.Span{TraceId: f.traceID(id), SpanId: f.spanID(id, role), TraceState: "lab=local", Name: name, Kind: kind,
		StartTimeUnixNano: start + offset, EndTimeUnixNano: start + offset + duration, Attributes: attrs, Status: status, Flags: 1}
	if role != 0 {
		result.ParentSpanId = f.spanID(id, 0)
	}
	result.Events = []*tracepb.Span_Event{{TimeUnixNano: start + offset + 100000, Name: "operation.started", Attributes: []*common.KeyValue{integer("request.id", int64(id))}}}
	if role == 0 && id > 0 {
		result.Links = []*tracepb.Span_Link{{TraceId: f.traceID(id - 1), SpanId: f.spanID(id-1, 0), Attributes: []*common.KeyValue{text("relationship", "previous-checkout")}, Flags: 1}}
	}
	return result
}

func (f Fixture) traceRequest(first, last int) *traces.ExportTraceServiceRequest {
	services := []string{"checkout", "inventory", "payment"}
	result := &traces.ExportTraceServiceRequest{}
	for role, service := range services {
		spans := make([]*tracepb.Span, 0, last-first)
		for id := first; id < last; id++ {
			spans = append(spans, f.span(id, role))
		}
		result.ResourceSpans = append(result.ResourceSpans, &tracepb.ResourceSpans{Resource: f.resource(service), ScopeSpans: []*tracepb.ScopeSpans{{Scope: scope(), Spans: spans}}})
	}
	return result
}

type LogBody struct {
	ID      int    `json:"id"`
	Request int    `json:"request"`
	Tenant  string `json:"tenant"`
	Status  string `json:"status"`
	TraceID string `json:"trace_id"`
	Payload string `json:"payload"`
}

func (f Fixture) body(id int) LogBody {
	request := id / f.LogsPerRequest
	payload := strings.Repeat("checkout-cart-", 20)
	if id%100 == 0 {
		var b strings.Builder
		for part := 0; part < 64; part++ {
			sum := sha256.Sum256([]byte(fmt.Sprintf("%s/%d/%d", f.PayloadSeed, id, part)))
			b.WriteString(hex.EncodeToString(sum[:]))
		}
		payload = b.String()
	}
	status := "ok"
	if incident(request) {
		status = "payment-error"
	}
	return LogBody{ID: id, Request: request, Tenant: tenant(request), Status: status, TraceID: hex.EncodeToString(f.traceID(request)), Payload: payload}
}
func (f Fixture) logStamp(id int) uint64 {
	return uint64(f.StartMS+int64(id/f.LogsPerRequest)*10+int64((id%f.LogsPerRequest)%5)) * 1000000
}
func (f Fixture) logRequest(first, last int) *logs.ExportLogsServiceRequest {
	records := make([]*logpb.LogRecord, 0, last-first)
	for id := first; id < last; id++ {
		request := id / f.LogsPerRequest
		body, _ := json.Marshal(f.body(id))
		level, name := logpb.SeverityNumber_SEVERITY_NUMBER_INFO, "INFO"
		role := 0
		if incident(request) {
			level, name, role = logpb.SeverityNumber_SEVERITY_NUMBER_ERROR, "ERROR", 2
		}
		records = append(records, &logpb.LogRecord{TimeUnixNano: f.logStamp(id), ObservedTimeUnixNano: f.logStamp(id), SeverityNumber: level, SeverityText: name,
			Body: &common.AnyValue{Value: &common.AnyValue_StringValue{StringValue: string(body)}}, TraceId: f.traceID(request), SpanId: f.spanID(request, role), Flags: 1,
			Attributes: []*common.KeyValue{text("run", f.Run), text("tenant", tenant(request)), integer("log.id", int64(id))}})
	}
	return &logs.ExportLogsServiceRequest{ResourceLogs: []*logpb.ResourceLogs{{Resource: f.resource("checkout"), ScopeLogs: []*logpb.ScopeLogs{{Scope: scope(), LogRecords: records}}}}}
}
func (f Fixture) metricValue(index int) (int, int64, float64) {
	series, point := index%f.Requests, index/f.Requests
	value := float64(5000 + series%10 + point)
	if incident(series) && point >= 40 && point < 45 {
		value += 45000
	}
	return series, f.StartMS + int64(point)*1000, value
}
func (f Fixture) metricRequest(first, last int) *metrics.ExportMetricsServiceRequest {
	values := make([]*metricpb.NumberDataPoint, 0, last-first)
	for id := first; id < last; id++ {
		series, stamp, value := f.metricValue(id)
		values = append(values, &metricpb.NumberDataPoint{TimeUnixNano: uint64(stamp) * 1000000, Attributes: []*common.KeyValue{text("run", f.Run), text("tenant", tenant(series)), text("series", strconv.Itoa(series))}, Value: &metricpb.NumberDataPoint_AsDouble{AsDouble: value}})
	}
	return &metrics.ExportMetricsServiceRequest{ResourceMetrics: []*metricpb.ResourceMetrics{{Resource: f.resource("checkout"), ScopeMetrics: []*metricpb.ScopeMetrics{{Scope: scope(), Metrics: []*metricpb.Metric{{Name: "checkout_latency_microseconds", Unit: "us", Data: &metricpb.Metric_Gauge{Gauge: &metricpb.Gauge{DataPoints: values}}}}}}}}}
}
