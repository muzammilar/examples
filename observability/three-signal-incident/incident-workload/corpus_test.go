package main

import (
	"bytes"
	"encoding/hex"
	"reflect"
	"testing"

	logs "go.opentelemetry.io/proto/otlp/collector/logs/v1"
	"google.golang.org/protobuf/proto"
)

func fixtureForTest() Fixture {
	return Fixture{Run: "0123456789abcdef0123456789abcdef", PayloadSeed: "abcdef0123456789abcdef0123456789", StartMS: 1795851117000, Requests: 10000, LogsPerRequest: 100, MetricPoints: 100}
}

func TestIncidentCountsAndTraceTree(t *testing.T) {
	f := fixtureForTest()
	failures := 0
	for id := 0; id < f.Requests; id++ {
		if incident(id) {
			failures++
		}
		root := f.span(id, 0)
		for role := 1; role < 3; role++ {
			child := f.span(id, role)
			if !bytes.Equal(child.TraceId, root.TraceId) || !bytes.Equal(child.ParentSpanId, root.SpanId) || child.StartTimeUnixNano < root.StartTimeUnixNano || child.EndTimeUnixNano > root.EndTimeUnixNano {
				t.Fatalf("invalid trace tree request=%d role=%d", id, role)
			}
		}
	}
	if failures != 250 {
		t.Fatalf("failed requests=%d", failures)
	}
	if incident(3998) || !incident(4000) || incident(4001) || !incident(4498) || incident(4500) {
		t.Fatal("incident boundary changed")
	}
}

func TestCanonicalLogPreservesItsSourceCorrelation(t *testing.T) {
	f := fixtureForTest()
	id := 4000 * f.LogsPerRequest
	body := f.body(id)
	record := f.logRequest(id, id+1).ResourceLogs[0].ScopeLogs[0].LogRecords[0]
	if body.Status != "payment-error" || body.Request != 4000 || body.TraceID != hex.EncodeToString(record.TraceId) || !bytes.Equal(record.SpanId, f.spanID(4000, 2)) || record.TimeUnixNano != f.logStamp(id) {
		t.Fatal("log correlation differs from incident source")
	}
	if len(body.Payload) != 4096 {
		t.Fatalf("rare payload bytes=%d", len(body.Payload))
	}
}

func TestPartialCollectorResponseDoesNotAcknowledgeAllRecords(t *testing.T) {
	raw, err := proto.Marshal(&logs.ExportLogsServiceResponse{PartialSuccess: &logs.ExportLogsPartialSuccess{RejectedLogRecords: 1}})
	if err != nil {
		t.Fatal(err)
	}
	if responseAccepted("logs", raw) == nil {
		t.Fatal("partial response was accepted")
	}
	if err = responseAccepted("logs", nil); err != nil {
		t.Fatal("empty protobuf success rejected:", err)
	}
	if responseAccepted("unknown", nil) == nil {
		t.Fatal("unknown signal accepted")
	}
}

func TestRawMetricLabelsCannotMergeDifferentTenants(t *testing.T) {
	f := fixtureForTest()
	labels := map[string]string{"__name__": "checkout_latency_microseconds", "run": f.Run, "series": "0", "tenant": "tenant-1"}
	if _, err := checkMetricLabels(labels, f); err == nil {
		t.Fatal("wrong indexed tenant accepted")
	}
	if _, err := timestampMS([]byte("1.0001")); err == nil {
		t.Fatal("sub-millisecond timestamp rounded into a valid sample")
	}
}

func TestNumericProjectionCannotBecomeExactFidelity(t *testing.T) {
	original, err := expectedSpan(fixtureForTest(), 1, 1)
	if err != nil {
		t.Fatal(err)
	}
	projected := stringAttributeProjection(original)
	if reflect.DeepEqual(original, projected) {
		t.Fatal("numeric types silently treated as exact")
	}
	if _, ok := original.Attributes["request.id"].(int64); !ok {
		t.Fatal("diagnostic mutated source oracle")
	}
	projected.ParentSpanID = "wrong-parent"
	if reflect.DeepEqual(projected, stringAttributeProjection(original)) {
		t.Fatal("projection hid a wrong parent")
	}
}
