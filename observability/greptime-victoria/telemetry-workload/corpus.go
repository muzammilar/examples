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
	common "go.opentelemetry.io/proto/otlp/common/v1"
	logpb "go.opentelemetry.io/proto/otlp/logs/v1"
	metricpb "go.opentelemetry.io/proto/otlp/metrics/v1"
	resourcepb "go.opentelemetry.io/proto/otlp/resource/v1"
)

// Fixture fixes the exact logical input. It is shared across both backends and
// survives recovery; timestamp/series identity never depends on request batching.
type Fixture struct {
	Run         string `json:"run"`
	PayloadSeed string `json:"payload_seed,omitempty"`
	StartMS     int64  `json:"start_ms"`
	Series      int    `json:"series"`
	Points      int    `json:"points"`
	Logs        int    `json:"logs"`
}

type LogBody struct {
	ID        int    `json:"id"`
	Tenant    string `json:"tenant"`
	Status    string `json:"status"`
	LatencyUS int    `json:"latency_us"`
	Payload   string `json:"payload"`
}

func attribute(key, value string) *common.KeyValue {
	return &common.KeyValue{Key: key, Value: &common.AnyValue{Value: &common.AnyValue_StringValue{StringValue: value}}}
}

func (f Fixture) resource() *resourcepb.Resource {
	return &resourcepb.Resource{Attributes: []*common.KeyValue{attribute("service.name", "checkout-lab")}}
}

func tenant(series int) string {
	// Half the series belong to one tenant; remaining series span 99 tenants.
	if series%2 == 0 {
		return "tenant-0"
	}
	return fmt.Sprintf("tenant-%d", 1+(series/2)%99)
}

func (f Fixture) metricValue(index int) (int, int64, float64) {
	series, point := index%f.Series, index/f.Series
	return series, f.StartMS + int64(point)*1000, float64(series*1000 + point)
}

func (f Fixture) metricRequest(first, last int) *metrics.ExportMetricsServiceRequest {
	points := make([]*metricpb.NumberDataPoint, 0, last-first)
	for i := first; i < last; i++ {
		series, stamp, value := f.metricValue(i)
		points = append(points, &metricpb.NumberDataPoint{
			Attributes:   []*common.KeyValue{attribute("run", f.Run), attribute("tenant", tenant(series)), attribute("series", strconv.Itoa(series))},
			TimeUnixNano: uint64(stamp) * 1_000_000,
			Value:        &metricpb.NumberDataPoint_AsDouble{AsDouble: value},
		})
	}
	return &metrics.ExportMetricsServiceRequest{ResourceMetrics: []*metricpb.ResourceMetrics{{
		Resource: f.resource(), ScopeMetrics: []*metricpb.ScopeMetrics{{
			Scope:   &common.InstrumentationScope{Name: "incident-corpus-v1"},
			Metrics: []*metricpb.Metric{{Name: "fleet_value", Data: &metricpb.Metric_Gauge{Gauge: &metricpb.Gauge{DataPoints: points}}}},
		}},
	}}}
}

func (f Fixture) logBody(index int) LogBody {
	// Mostly repetitive bodies; every hundredth body has a larger deterministic
	// high-entropy hexadecimal tail. Report this synthetic compressibility mix.
	payload := strings.Repeat("checkout-cart-", 20)
	seed := f.PayloadSeed
	if seed == "" {
		seed = f.Run
	}
	if index%100 == 0 {
		var value strings.Builder
		for j := 0; j < 64; j++ {
			sum := sha256.Sum256([]byte(fmt.Sprintf("%s:%d:%d", seed, index, j)))
			value.WriteString(hex.EncodeToString(sum[:]))
		}
		payload = value.String()
	}
	status := "ok"
	if index%17 == 0 {
		status = "error"
	}
	return LogBody{ID: index, Tenant: tenant(index % f.Series), Status: status, LatencyUS: 100 + (index*37)%100000, Payload: payload}
}

func (f Fixture) logRequest(first, last int) (*logs.ExportLogsServiceRequest, error) {
	records := make([]*logpb.LogRecord, 0, last-first)
	for i := first; i < last; i++ {
		body := f.logBody(i)
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		records = append(records, &logpb.LogRecord{
			TimeUnixNano: uint64(f.StartMS+int64(i)) * 1_000_000,
			SeverityText: strings.ToUpper(body.Status),
			Body:         &common.AnyValue{Value: &common.AnyValue_StringValue{StringValue: string(encoded)}},
			Attributes:   []*common.KeyValue{attribute("run", f.Run), attribute("tenant", body.Tenant)},
		})
	}
	return &logs.ExportLogsServiceRequest{ResourceLogs: []*logpb.ResourceLogs{{Resource: f.resource(), ScopeLogs: []*logpb.ScopeLogs{{Scope: &common.InstrumentationScope{Name: "incident-corpus-v1"}, LogRecords: records}}}}}, nil
}
