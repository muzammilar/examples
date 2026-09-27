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

// StartMetricsServer exposes the franz-go client metrics (via the kprom plugin) on `addr`/metrics,
// including produced/fetched records and batches and the end-to-end request latency histogram,
// and returns the kgo option that hooks the metrics into a client.
func StartMetricsServer(namespace, addr string, logger *slog.Logger) kgo.Opt {
	registry := prometheus.NewRegistry()
	metrics := kprom.NewMetrics(namespace,
		kprom.Registry(registry),
		// the defaults (uncompressed bytes by topic and node) plus record and batch counters for produce/fetch
		kprom.FetchAndProduceDetail(kprom.ByNode, kprom.ByTopic, kprom.UncompressedBytes, kprom.Records, kprom.Batches),
		// end-to-end request latency per broker (histogram)
		kprom.Histograms(kprom.RequestDurationE2E),
	)

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	go func() {
		if err := http.ListenAndServe(addr, mux); err != nil {
			logger.Error("metrics server stopped", "addr", addr, "err", err)
		}
	}()

	return kgo.WithHooks(metrics)
}
