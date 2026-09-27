// The common package contains the shared code between the admin, producer and consumer binaries

package common

import (
	"log/slog"
	"net/http"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/twmb/franz-go/pkg/kgo"
	"github.com/twmb/franz-go/plugin/kprom"
)

// StartMetricsServer exposes the franz-go client metrics (via the kprom plugin) on `addr`/metrics
// and returns the kgo option that hooks the metrics into a client.
func StartMetricsServer(namespace, addr string, logger *slog.Logger) kgo.Opt {
	registry := prometheus.NewRegistry()
	metrics := kprom.NewMetrics(namespace, kprom.Registry(registry))

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			logger.Error("metrics server stopped", "addr", addr, "err", err)
		}
	}()

	return kgo.WithHooks(metrics)
}
