package main

import (
	"encoding/json"
	"google.golang.org/protobuf/proto"
	"testing"
)

func TestBatchBoundariesDoNotChangeMetricIdentity(t *testing.T) {
	f := Fixture{Run: "test", StartMS: 100000, Series: 7, Points: 3, Logs: 21}
	all := f.metricRequest(0, 21).ResourceMetrics[0].ScopeMetrics[0].Metrics[0].GetGauge().DataPoints
	for first := 0; first < 21; first += 5 {
		last := min(first+5, 21)
		part := f.metricRequest(first, last).ResourceMetrics[0].ScopeMetrics[0].Metrics[0].GetGauge().DataPoints
		for i, row := range part {
			if !proto.Equal(row, all[first+i]) {
				t.Fatal("batch changed point identity")
			}
		}
	}
}

func TestLogBodyAndTimeMatchIndependentID(t *testing.T) {
	f := Fixture{Run: "test", StartMS: 100000, Series: 7, Points: 3, Logs: 21}
	request, err := f.logRequest(3, 13)
	if err != nil {
		t.Fatal(err)
	}
	rows := request.ResourceLogs[0].ScopeLogs[0].LogRecords
	for i, row := range rows {
		var body LogBody
		if err = json.Unmarshal([]byte(row.Body.GetStringValue()), &body); err != nil {
			t.Fatal(err)
		}
		if body.ID != i+3 || row.TimeUnixNano != uint64(f.StartMS+int64(body.ID))*1_000_000 {
			t.Fatal("log ID/timestamp mismatch")
		}
		if body != f.logBody(body.ID) {
			t.Fatal("body not reproducible")
		}
	}
}

func TestIndependentNamespacesCanShareExactPayloadSeed(t *testing.T) {
	first := Fixture{Run: "first", PayloadSeed: "fixed", Series: 100}
	second := Fixture{Run: "second", PayloadSeed: "fixed", Series: 100}
	for _, id := range []int{0, 1, 100, 10000} {
		if first.logBody(id) != second.logBody(id) {
			t.Fatal("namespace changed fixed corpus body")
		}
	}
}
