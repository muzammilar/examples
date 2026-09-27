// The common package contains the shared code between both producer and consumer

package common

import (
	"net/http"
	"time"

	"github.com/deathowl/go-metrics-prometheus"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/rcrowley/go-metrics"
)

// metrics registry
var MetricsRegistry metrics.Registry

//StartMetricsCollector starts the metric subsystem with an internal registry on port 8080 (and periodically updates)
func StartMetricsCollector(subsystem string) {
	// initialize a new metrics registry
	MetricsRegistry = metrics.NewRegistry() // alternatively we can use metrics.DefaultRegistry
	// setup the prometheus client for go-metrics
	prometheusClient := prometheusmetrics.NewPrometheusProvider(
		MetricsRegistry,              // registry
		"trees",                      // system
		subsystem,                    //subsystem
		prometheus.DefaultRegisterer, // prometheus registerer
		1*time.Second,                // update frequency
	)
	// start metrics handler
	go func() {
		http.Handle("/metrics", promhttp.Handler())
		http.ListenAndServe(":8080", nil) // hard code metrics port for now
	}()
	// start periodic updater
	go prometheusClient.UpdatePrometheusMetrics()

}
