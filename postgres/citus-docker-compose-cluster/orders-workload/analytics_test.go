package main

import (
	"testing"
	"time"
)

func TestSmallSampleP99IncludesSlowRequest(t *testing.T) {
	samples := make([]float64, 25)
	for i := range samples {
		samples[i] = 1
	}
	samples[0] = 100
	result := analyticsStats(samples, time.Second)
	if result.P99 != 100 || result.Max != 100 || result.P50 != 1 || result.Samples[0] != 100 || result.Samples[24] != 1 {
		t.Fatalf("slow request hidden or raw samples lost: %+v", result)
	}
}
