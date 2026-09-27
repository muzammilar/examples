package main

import (
	"math"
	"time"
)

// Metric is a generic struct to capture the metric being passed
type Metric struct {
	TS    time.Time
	Value float64
}

// MetricStats keeps tracks of metrics sent. It's a counter
type MetricStats struct {
	TS      time.Time
	Total   uint64
	Sent    uint64
	Dropped uint64
}

// GaugeSummary is a struct that stores the summary of the Metrics per interval
type GaugeSummary struct {
	// metadata
	metricsLag time.Duration // the time lag between the current time and the incoming metric
	// Metrics
	TS         time.Time // the timestamp of the latest Metric
	TimeBucket uint64    // the modulo of the TS (timestamp) value to the bucket interval. This is not the bucket ID, but the absoulte bucket number if there were infinte buckets
	Sum        float64
	Count      float64 // it is a float for ease of computation
	// Mean Metrics
	Avg float64
	// Median Metrics
	Min float64
	Max float64
}

// Reset a GuageSummary metric to the default values
func (g *GaugeSummary) Reset() {
	// metadata
	g.metricsLag = 0
	// guage metrics
	g.TS = time.Time{}
	g.TimeBucket = 0
	g.Sum = 0
	g.Count = 0
	g.Avg = 0
	g.Max = -math.MaxFloat64
	g.Min = math.MaxFloat64
}

func (g *GaugeSummary) AddMetricDataPoint(curTime time.Time, curTimeBucket uint64, m *Metric) {
	// since Metric TS would always be increasing in our case, but is not true for every case, we use time.Now() instead
	g.TS = curTime
	g.TimeBucket = curTimeBucket

	// mean
	g.Sum += m.Value
	g.Count += 1
	g.Avg = g.Sum / g.Count

	// median related metrics
	g.Min = math.Min(g.Min, m.Value)
	g.Max = math.Max(g.Max, m.Value)

	// metadata
	g.metricsLag = g.TS.Sub(m.TS)
}
