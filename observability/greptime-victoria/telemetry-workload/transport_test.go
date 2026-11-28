package main

import (
	"bytes"
	"compress/gzip"
	"io"
	"testing"

	metrics "go.opentelemetry.io/proto/otlp/collector/metrics/v1"
	"google.golang.org/protobuf/proto"
)

func TestPartialAcceptanceIsNotFullAcceptance(t *testing.T) {
	body, err := proto.Marshal(&metrics.ExportMetricsServiceResponse{PartialSuccess: &metrics.ExportMetricsPartialSuccess{RejectedDataPoints: 1, ErrorMessage: "invalid point"}})
	if err != nil {
		t.Fatal(err)
	}
	if acceptResponse("metrics", body) == nil {
		t.Fatal("partial response classified as full acceptance")
	}
	if err = acceptResponse("metrics", nil); err != nil {
		t.Fatal("empty protobuf success must be allowed", err)
	}
	if acceptResponse("metrics", []byte(`{"error":"not protobuf"}`)) == nil {
		t.Fatal("non-protobuf success accepted")
	}
}

func TestCompressionPreservesExactProtobuf(t *testing.T) {
	f := Fixture{Run: "test", StartMS: 100000, Series: 7, Points: 3, Logs: 21}
	raw, wire, err := encodeRequest(f.metricRequest(0, 21), true)
	if err != nil {
		t.Fatal(err)
	}
	reader, err := gzip.NewReader(bytes.NewReader(wire))
	if err != nil {
		t.Fatal(err)
	}
	decoded, err := io.ReadAll(reader)
	if err != nil {
		t.Fatal(err)
	}
	if err = reader.Close(); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(raw, decoded) {
		t.Fatal("compression changed request")
	}
}

func TestOnlyOwnedEndpoints(t *testing.T) {
	for _, address := range []string{"https://greptime:4000", "http://localhost:4000", "http://example.invalid:4000", "http://user:pass@greptime:4000", "http://greptime:4001"} {
		if localURL(address) == nil {
			t.Fatalf("allowed unexpected endpoint %q", address)
		}
	}
	if err := localURL("http://greptime:4000/v1/otlp/v1/metrics"); err != nil {
		t.Fatal(err)
	}
}
