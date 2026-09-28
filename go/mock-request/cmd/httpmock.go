package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/muzammilar/mockrequest/request"
)

// urlList is a flag.Value that accepts a comma-separated list and can be repeated.
type urlList []string

func (u *urlList) String() string { return strings.Join(*u, ",") }

func (u *urlList) Set(v string) error {
	for _, s := range strings.Split(v, ",") {
		if s = strings.TrimSpace(s); s != "" {
			*u = append(*u, s)
		}
	}
	return nil
}

func main() {
	var urls urlList
	url := flag.String("url", "https://example.com", "URL to fetch (ignored when -urls is set)")
	flag.Var(&urls, "urls", "comma-separated URLs to fetch; may be repeated")
	timeout := flag.Duration("timeout", 10*time.Second, "per-request timeout")
	interval := flag.Duration("interval", 0, "probe every interval until SIGINT/SIGTERM (0 = fetch once and exit)")
	metricsAddr := flag.String("metrics-addr", "", "serve Prometheus metrics on this address, e.g. :8080 (empty = disabled)")
	flag.Parse()
	if len(urls) == 0 {
		urls = urlList{*url}
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// *http.Client satisfies request.Doer, so production code uses a real
	// client. The metrics decorator is also a Doer, so it slots in between
	// without the Fetcher knowing.
	var doer request.Doer = &http.Client{}
	if *metricsAddr != "" {
		reg := prometheus.NewRegistry()
		reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
		doer = request.NewInstrumentedDoer(doer, request.NewMetrics(reg))
		srv := serveMetrics(*metricsAddr, reg)
		defer shutdown(srv)
	}
	f := request.NewFetcher(doer)

	if *interval <= 0 {
		if failed := probe(ctx, f, urls, *timeout); failed > 0 {
			stop()
			os.Exit(1)
		}
		return
	}

	log.Printf("probing %d url(s) every %s", len(urls), *interval)
	ticker := time.NewTicker(*interval)
	defer ticker.Stop()
	for {
		probe(ctx, f, urls, *timeout)
		select {
		case <-ctx.Done():
			log.Print("shutting down")
			return
		case <-ticker.C:
		}
	}
}

// probe fetches every URL once and returns the number of failures.
func probe(ctx context.Context, f *request.Fetcher, urls []string, timeout time.Duration) (failed int) {
	for _, u := range urls {
		reqCtx, cancel := context.WithTimeout(ctx, timeout)
		page, err := f.Fetch(reqCtx, u)
		cancel()
		if err != nil {
			failed++
			log.Printf("%s -> error: %v", u, err)
			continue
		}
		fmt.Printf("%s -> %d (%d bytes)\n", page.URL, page.StatusCode, page.Bytes)
	}
	return failed
}

func serveMetrics(addr string, reg *prometheus.Registry) *http.Server {
	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{Registry: reg}))
	srv := &http.Server{Addr: addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	go func() {
		log.Printf("serving metrics on %s/metrics", addr)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("metrics server: %v", err)
		}
	}()
	return srv
}

func shutdown(srv *http.Server) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_ = srv.Shutdown(ctx)
}
