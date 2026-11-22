package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

// This proxy is a local read-only fault shim, not an S3 implementation. Keep the
// incoming signed Host, escaped path and query when changing only the dial target.
type manifestProbeProxy struct {
	prefix  string
	drop    bool
	forward *httputil.ReverseProxy
	emit    func(any) error
	mu      sync.Mutex
	failure error
	dropped int
}

func (p *manifestProbeProxy) record(r *http.Request, action string, detail error) bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	entry := map[string]any{"ns": time.Now().UnixNano(), "method": r.Method, "request_uri": r.RequestURI, "signed_host": r.Host, "action": action}
	if detail != nil {
		entry["error"] = detail.Error()
	}
	if err := p.emit(entry); err != nil {
		p.failure = errors.Join(p.failure, err)
		return false
	}
	return true
}

func (p *manifestProbeProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	_, err := recoveryWarehouseURI("s3://warehouse" + strings.TrimPrefix(r.URL.EscapedPath(), "/warehouse"))
	if err != nil || (r.Method != http.MethodGet && r.Method != http.MethodHead) ||
		!(strings.HasPrefix(r.URL.Path, p.prefix+"metadata/") || strings.HasPrefix(r.URL.Path, p.prefix+"data/")) {
		http.Error(w, "only owned table GET/HEAD allowed", http.StatusForbidden)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, p.prefix+"metadata/")
	if p.drop && r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, p.prefix+"metadata/") && strings.HasSuffix(name, ".avro") && !strings.HasPrefix(name, "snap-") {
		hijacker, ok := w.(http.Hijacker)
		if !ok {
			http.Error(w, "HTTP/1 hijacking required", http.StatusInternalServerError)
			return
		}
		connection, _, err := hijacker.Hijack()
		if err != nil {
			p.record(r, "hijack-error", err)
			return
		}
		// Fsync the exact target before closing its connection. A native abort can
		// kill this process before a second post-close observation is persisted.
		if !p.record(r, "manifest-connection-close-intent", nil) {
			_ = connection.Close()
			return
		}
		p.mu.Lock()
		p.dropped++
		p.mu.Unlock()
		p.record(r, "manifest-connection-closed", connection.Close())
		return
	}
	if !p.record(r, "forward", nil) {
		http.Error(w, "witness fsync failed", http.StatusInternalServerError)
		return
	}
	p.forward.ServeHTTP(w, r)
}

func newManifestProbeProxy(prefix string, drop bool, emit func(any) error) (*manifestProbeProxy, *http.Transport) {
	p := &manifestProbeProxy{prefix: prefix, drop: drop, emit: emit}
	transport := &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext, ResponseHeaderTimeout: 15 * time.Second}
	p.forward = &httputil.ReverseProxy{
		Rewrite: func(r *httputil.ProxyRequest) {
			r.Out.URL.Scheme, r.Out.URL.Host = "http", "storage:8333"
			r.Out.Host = r.In.Host
			r.Out.URL.RawPath, r.Out.URL.RawQuery = r.In.URL.RawPath, r.In.URL.RawQuery
		},
		Transport: transport,
		ModifyResponse: func(r *http.Response) error {
			if r.StatusCode >= 300 && r.StatusCode < 400 {
				return errors.New("local backend redirects are forbidden")
			}
			return nil
		},
		ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
			p.record(r, "backend-error", err)
			http.Error(w, "local storage request failed", http.StatusBadGateway)
		},
	}
	return p, transport
}

func recoveryManifestProbe() (returned error) {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	mode := os.Getenv("RECOVERY_MANIFEST_MODE")
	if mode != "healthy" && mode != "drop-data-manifest" {
		return errors.New("RECOVERY_MANIFEST_MODE must be healthy or drop-data-manifest")
	}
	snapshot, err := strconv.ParseInt(os.Getenv("RECOVERY_PROBE_SNAPSHOT"), 10, 64)
	if err != nil {
		return err
	}
	state := recoverySnapshot{UUID: os.Getenv("RECOVERY_PROBE_TABLE_UUID"), Snapshot: snapshot, Metadata: os.Getenv("RECOVERY_PROBE_METADATA")}
	dataPrefix, err := recoveryInspectionDataPrefix(c, state)
	if err != nil {
		return err
	}
	metadataPath, _ := recoveryWarehouseURI(state.Metadata)
	if !strings.HasSuffix(metadataPath, ".metadata.json") {
		return errors.New("explicit owned metadata JSON required")
	}
	directory, err := os.MkdirTemp(root, "manifest-probe-")
	if err != nil {
		return err
	}
	if err = recoverySyncDir(root); err != nil {
		return err
	}
	journal, err := recoveryOpenJournal(directory, "requests")
	if err != nil {
		return err
	}
	proxy, transport := newManifestProbeProxy("/warehouse"+strings.TrimPrefix(strings.TrimSuffix(dataPrefix, "data/"), "s3://warehouse"), mode == "drop-data-manifest", journal.emit)
	server := httptest.NewServer(proxy)
	defer transport.CloseIdleConnections()
	var db *sql.DB
	report := map[string]any{"run_id": c.RunID, "config_sha256": digest, "mode": mode, "snapshot": state, "started_ns": time.Now().UnixNano(), "native_before": recoveryNativeCounters()}
	defer func() {
		if db != nil {
			returned = errors.Join(returned, db.Close())
		}
		server.Close()
		proxy.mu.Lock()
		returned = errors.Join(returned, proxy.failure, journal.file.Close())
		report["dropped_requests"] = proxy.dropped
		proxy.mu.Unlock()
		report["completed_ns"], report["passed"], report["native_after"] = time.Now().UnixNano(), returned == nil, recoveryNativeCounters()
		if returned != nil {
			report["error"] = returned.Error()
		}
		returned = errors.Join(returned, recoverySave(filepath.Join(directory, "result.json"), report))
	}()
	if err = recoverySave(filepath.Join(directory, "started.json"), report); err != nil {
		return err
	}
	fmt.Println(directory)
	base, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	ctx, cancel := context.WithTimeout(base, time.Duration(c.VerifyTimeoutSeconds)*time.Second)
	defer cancel()
	db, err = sql.Open("duckdb", "")
	if err != nil {
		return err
	}
	db.SetMaxOpenConns(1)
	var engine string
	if err = db.QueryRowContext(ctx, "SELECT version()").Scan(&engine); err != nil {
		return err
	}
	if _, err = qualifiedExtensionPins(engine, expectedNativeVersion); err != nil {
		return err
	}
	report["native_engine_version"] = engine
	if err = recoverySave(filepath.Join(directory, "native-version.json"), map[string]string{"native_engine_version": engine}); err != nil {
		return err
	}
	for _, query := range []string{
		"SET threads=2", "SET memory_limit='512MB'", "SET temp_directory='/tmp/duckdb-recovery-spill'", "SET max_temp_directory_size='1GiB'", "SET extension_directory='/opt/duckdb/extensions'", "SET autoinstall_known_extensions=false", "SET allow_persistent_secrets=false", "LOAD httpfs", "LOAD iceberg", "SET http_retries=0", "SET http_timeout=5", "SET enable_global_s3_configuration=false",
		"CREATE SECRET manifest_probe (TYPE S3,KEY_ID 'demo',SECRET 'demo',ENDPOINT " + quote(strings.TrimPrefix(server.URL, "http://")) + ",URL_STYLE 'path',USE_SSL false,SCOPE " + quote(strings.TrimSuffix(dataPrefix, "data/")) + ")",
	} {
		if _, err = db.ExecContext(ctx, query); err != nil {
			return err
		}
	}
	var count, amount int64
	err = db.QueryRowContext(ctx, "SELECT count(*),sum(amount_cents) FROM iceberg_scan(?,snapshot_from_id=CAST(? AS UBIGINT))", state.Metadata, state.Snapshot).Scan(&count, &amount)
	report["rows"], report["amount_sum"] = count, amount
	if err == nil && mode == "drop-data-manifest" {
		return errors.New("fault mode unexpectedly completed; inspect exact request history")
	}
	return err // A normal SQL error remains a failing exit, unlike native SIGABRT.
}
