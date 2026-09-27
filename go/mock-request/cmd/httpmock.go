package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/muzammilar/mockrequest/request"
)

func main() {
	// parse the url flag and perform a request to the website
	url := flag.String("url", "https://example.com", "URL to fetch")
	timeout := flag.Duration("timeout", 10*time.Second, "request timeout")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), *timeout)
	defer cancel()

	// *http.Client satisfies request.Doer, so production code uses a real client.
	f := request.NewFetcher(&http.Client{})
	page, err := f.Fetch(ctx, *url)
	if err != nil {
		log.Fatal(err)
	}
	fmt.Printf("%s -> %d (%d bytes)\n", page.URL, page.StatusCode, page.Bytes)
}
