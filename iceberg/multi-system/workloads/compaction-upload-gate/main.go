package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func localTransport() *http.Transport {
	return &http.Transport{
		Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext,
		DisableCompression: true, ResponseHeaderTimeout: 30 * time.Second,
		MaxIdleConnsPerHost: 8, IdleConnTimeout: 30 * time.Second,
	}
}

func serve() error {
	cfg, err := loadConfig(os.Getenv)
	if err != nil {
		return err
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer cancel()
	transport := localTransport()
	defer transport.CloseIdleConnections()
	g, err := newGate(ctx, cfg, transport)
	if err != nil {
		return err
	}
	servers := []*http.Server{
		{Addr: ":8333", Handler: g, ReadHeaderTimeout: 5 * time.Second},
		{Addr: "127.0.0.1:8182", Handler: g.admin(), ReadHeaderTimeout: 5 * time.Second},
	}
	stopped := make(chan error, len(servers))
	for _, server := range servers {
		go func() { stopped <- server.ListenAndServe() }()
	}
	select {
	case <-ctx.Done():
	case err = <-stopped:
		cancel()
	}
	if errors.Is(err, http.ErrServerClosed) {
		err = nil
	}
	cancel() // Held responses abort; they are never released by shutdown.
	deadline, stop := context.WithTimeout(context.Background(), 5*time.Second)
	defer stop()
	for _, server := range servers {
		if shutdownErr := server.Shutdown(deadline); shutdownErr != nil {
			err = errors.Join(err, shutdownErr, server.Close())
		}
	}
	g.mu.Lock()
	if g.fatal != "" {
		err = errors.Join(err, errors.New(g.fatal))
	}
	g.mu.Unlock()
	return err
}

func status() error {
	client := &http.Client{Transport: localTransport(), Timeout: 3 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	defer client.CloseIdleConnections()
	response, err := client.Get("http://127.0.0.1:8182/status")
	if err != nil {
		return err
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, (8<<20)+1))
	if err != nil || len(body) > 8<<20 || response.StatusCode != http.StatusOK || !json.Valid(body) {
		return errors.Join(err, errors.New("gate status unavailable or invalid"))
	}
	if _, err = os.Stdout.Write(body); err != nil {
		return err
	}
	var value struct {
		FatalError string `json:"fatal_error"`
	}
	if err = json.Unmarshal(body, &value); err == nil && value.FatalError != "" {
		return errors.New("gate reports a fatal journal error")
	}
	return err
}

func main() {
	log.SetFlags(log.LstdFlags | log.LUTC)
	var err error
	switch {
	case len(os.Args) == 1 || len(os.Args) == 2 && os.Args[1] == "serve":
		err = serve()
	case len(os.Args) == 2 && os.Args[1] == "status":
		err = status()
	default:
		err = fmt.Errorf("usage: upload-gate [serve|status]")
	}
	if err != nil {
		log.Print(err)
		os.Exit(1)
	}
}
