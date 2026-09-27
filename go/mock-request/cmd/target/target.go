// Command target is a tiny local HTTP service for the docker compose demo. It
// gives the prober a mix of status codes, body sizes and latencies to graph,
// without depending on the public internet.
//
//	/ok      200, 0-150ms, 8-16KiB body
//	/slow    200, 0-1.5s (requests beyond the prober's -timeout become errors)
//	/flaky   200 (70%), 429 (15%) or 503 (15%), 0-300ms
//	/missing 404
//	/error   500
package main

import (
	"flag"
	"log"
	"math/rand/v2"
	"net/http"
	"strings"
	"time"
)

func main() {
	addr := flag.String("addr", ":8081", "listen address")
	flag.Parse()

	mux := http.NewServeMux()
	mux.HandleFunc("/ok", handler(150*time.Millisecond, 16*1024, func() int { return http.StatusOK }))
	mux.HandleFunc("/slow", handler(1500*time.Millisecond, 1024, func() int { return http.StatusOK }))
	mux.HandleFunc("/flaky", handler(300*time.Millisecond, 256, func() int {
		switch r := rand.IntN(100); {
		case r < 70:
			return http.StatusOK
		case r < 85:
			return http.StatusTooManyRequests
		default:
			return http.StatusServiceUnavailable
		}
	}))
	mux.HandleFunc("/missing", handler(20*time.Millisecond, 64, func() int { return http.StatusNotFound }))
	mux.HandleFunc("/error", handler(50*time.Millisecond, 128, func() int { return http.StatusInternalServerError }))

	log.Printf("target listening on %s", *addr)
	srv := &http.Server{Addr: *addr, Handler: mux, ReadHeaderTimeout: 5 * time.Second}
	log.Fatal(srv.ListenAndServe())
}

// handler sleeps for a random duration up to maxDelay, then writes a body of
// roughly size bytes with the status returned by code.
func handler(maxDelay time.Duration, size int, code func() int) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(rand.N(maxDelay)):
		case <-r.Context().Done():
			return
		}
		n := size/2 + rand.IntN(size/2+1)
		w.Header().Set("Content-Type", "text/plain")
		w.WriteHeader(code())
		_, _ = w.Write([]byte(strings.Repeat("x", n)))
	}
}
