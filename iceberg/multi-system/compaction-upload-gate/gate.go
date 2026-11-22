package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"encoding/xml"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

const upstream = "http://storage:8333"

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
var tableUUIDPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type config struct {
	RunID         string `json:"run_id"`
	Prefix        string `json:"object_prefix"`
	Results       string `json:"-"`
	HoldFinalACKs bool   `json:"hold_final_acks"`
}

func loadConfig(env func(string) string) (config, error) {
	c := config{RunID: env("GATE_RUN_ID"), Prefix: env("GATE_OBJECT_PREFIX"), Results: env("GATE_RESULTS_DIR")}
	switch env("GATE_HOLD_FINAL_ACKS") {
	case "", "true":
		c.HoldFinalACKs = true
	case "false":
	default:
		return c, errors.New("GATE_HOLD_FINAL_ACKS must be true or false")
	}
	if c.Results == "" {
		c.Results = "/results"
	}
	if !uuidPattern.MatchString(c.RunID) {
		return c, errors.New("GATE_RUN_ID must be a canonical lowercase v4 UUID")
	}
	base := "s3://warehouse/lab/recovery_" + strings.ReplaceAll(c.RunID, "-", "") + "-"
	if !strings.HasPrefix(c.Prefix, base) || !strings.HasSuffix(c.Prefix, "/data/") || !tableUUIDPattern.MatchString(strings.TrimSuffix(strings.TrimPrefix(c.Prefix, base), "/data/")) {
		return c, errors.New("GATE_OBJECT_PREFIX must name this run's exact native table UUID data directory")
	}
	return c, nil
}

type record struct {
	Version               int    `json:"version"`
	RunID                 string `json:"run_id"`
	ConfigSHA             string `json:"config_sha256"`
	Prefix                string `json:"object_prefix"`
	ID                    string `json:"record_id"`
	Event                 string `json:"event"`
	Operation             string `json:"operation"`
	Method                string `json:"method"`
	ObjectURI             string `json:"object_uri"`
	RequestURI            string `json:"raw_request_uri"`
	RequestSHA            string `json:"request_uri_sha256"`
	AuthRedacted          bool   `json:"uri_auth_redacted"`
	StartedNS             int64  `json:"started_ns"`
	BackendCompletedNS    int64  `json:"backend_completed_ns"`
	EventNS               int64  `json:"event_ns"`
	RequestContentLength  int64  `json:"request_content_length"`
	StreamedRequestBytes  int64  `json:"streamed_request_bytes"`
	ResponseContentLength int64  `json:"response_content_length"`
	BackendStatus         int    `json:"backend_status"`
	AckHeld               bool   `json:"ack_held"`
	Error                 string `json:"error,omitempty"`
	JournalFile           string `json:"journal_file"`
}

type gate struct {
	cfg       config
	root      string
	configSHA string
	transport http.RoundTripper
	stop      context.Context
	mu        sync.Mutex
	held      map[string]record
	completed []record
	fatal     string
}

func immutable(path string, value any) (string, error) {
	body, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return "", err
	}
	body = append(body, '\n')
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return "", err
	}
	_, writeErr := f.Write(body)
	syncErr, closeErr := f.Sync(), f.Close()
	checksum := sha256.Sum256(body)
	return hex.EncodeToString(checksum[:]), errors.Join(writeErr, syncErr, closeErr, syncDirectory(filepath.Dir(path)))
}

func syncDirectory(path string) error {
	directory, err := os.Open(path)
	if err != nil {
		return err
	}
	return errors.Join(directory.Sync(), directory.Close())
}

func newGate(ctx context.Context, cfg config, transport http.RoundTripper) (*gate, error) {
	root := filepath.Join(cfg.Results, "upload-gate-"+cfg.RunID)
	if err := os.Mkdir(root, 0755); err != nil {
		return nil, fmt.Errorf("reserve new gate run; existing evidence cannot be reused: %w", err)
	}
	if err := syncDirectory(cfg.Results); err != nil {
		return nil, err
	}
	if err := os.Mkdir(filepath.Join(root, "requests"), 0755); err != nil {
		return nil, err
	}
	checksum, err := immutable(filepath.Join(root, "config.json"), map[string]any{"version": 1, "run_id": cfg.RunID, "object_prefix": cfg.Prefix, "upstream": upstream, "hold_final_acks": cfg.HoldFinalACKs})
	if err != nil {
		return nil, err
	}
	return &gate{cfg: cfg, root: root, configSHA: checksum, transport: transport, stop: ctx, held: make(map[string]record)}, nil
}

func authQuery(key string) bool {
	switch strings.ToLower(key) {
	case "x-amz-signature", "x-amz-credential", "x-amz-security-token", "awsaccesskeyid", "signature", "securitytoken":
		return true
	}
	return false
}

func safeRequestURI(r *http.Request) (string, bool) {
	query, redacted := strings.Split(r.URL.RawQuery, "&"), false
	for i, part := range query {
		key, _, _ := strings.Cut(part, "=")
		decoded, err := url.QueryUnescape(key)
		if err == nil && authQuery(decoded) {
			query[i], redacted = key+"=REDACTED", true
		}
	}
	value := r.URL.EscapedPath()
	if r.URL.ForceQuery || r.URL.RawQuery != "" {
		value += "?" + strings.Join(query, "&")
	}
	return value, redacted
}

func (g *gate) finalUpload(r *http.Request) (string, string) {
	prefix := "/warehouse/" + strings.TrimPrefix(g.cfg.Prefix, "s3://warehouse/")
	if !strings.HasPrefix(r.URL.Path, prefix) || !strings.HasSuffix(strings.ToLower(r.URL.Path), ".parquet") {
		return "", ""
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil {
		return "", ""
	}
	operation := ""
	if r.Method == http.MethodPut && !query.Has("uploadId") && !query.Has("partNumber") && !query.Has("uploads") && !query.Has("acl") && !query.Has("tagging") && !query.Has("retention") && !query.Has("legal-hold") {
		operation = "put-object"
	} else if r.Method == http.MethodPost && len(query["uploadId"]) == 1 && query.Get("uploadId") != "" && !query.Has("partNumber") && !query.Has("uploads") {
		operation = "complete-multipart-upload"
	}
	if operation == "" {
		return "", ""
	}
	object := &url.URL{Scheme: "s3", Host: "warehouse", Path: strings.TrimPrefix(r.URL.Path, "/warehouse")}
	return operation, object.String()
}

func removeHopHeaders(h http.Header) {
	for _, value := range h.Values("Connection") {
		for _, name := range strings.Split(value, ",") {
			h.Del(strings.TrimSpace(name))
		}
	}
	for _, name := range []string{"Connection", "Proxy-Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization", "TE", "Trailer", "Transfer-Encoding", "Upgrade"} {
		h.Del(name)
	}
}

type countedBody struct {
	io.ReadCloser
	bytes atomic.Int64
}

func (b *countedBody) Read(p []byte) (int, error) {
	n, err := b.ReadCloser.Read(p)
	b.bytes.Add(int64(n))
	return n, err
}

func (g *gate) failedJournal(w http.ResponseWriter, err error) {
	g.mu.Lock()
	g.fatal = "gate journal failed: " + err.Error()
	g.mu.Unlock()
	http.Error(w, "gate journal unavailable; acknowledgement refused", http.StatusServiceUnavailable)
}

func (g *gate) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	g.mu.Lock()
	fatal := g.fatal
	g.mu.Unlock()
	if fatal != "" {
		http.Error(w, "gate journal unavailable", http.StatusServiceUnavailable)
		return
	}
	if r.URL.IsAbs() || r.URL.Host != "" || r.Method == http.MethodConnect {
		http.Error(w, "origin-form S3 requests only", http.StatusBadRequest)
		return
	}
	query, queryErr := url.ParseQuery(r.URL.RawQuery)
	if queryErr != nil || len(query["uploadId"]) > 1 {
		http.Error(w, "invalid S3 query encoding", http.StatusBadRequest)
		return
	}
	for _, part := range strings.Split(r.URL.Path, "/") {
		if part == "." || part == ".." || strings.ContainsAny(part, "\\\x00\r\n") {
			http.Error(w, "unsafe S3 object path", http.StatusBadRequest)
			return
		}
	}
	operation, object := g.finalUpload(r)
	var value record
	var directory string
	if operation != "" {
		var err error
		directory, err = os.MkdirTemp(filepath.Join(g.root, "requests"), "request-")
		if err != nil {
			g.failedJournal(w, err)
			return
		}
		if err := syncDirectory(filepath.Dir(directory)); err != nil {
			g.failedJournal(w, err)
			return
		}
		uri, redacted := safeRequestURI(r)
		checksum := sha256.Sum256([]byte(r.URL.RequestURI()))
		value = record{Version: 1, RunID: g.cfg.RunID, ConfigSHA: g.configSHA, Prefix: g.cfg.Prefix, ID: filepath.Base(directory), Event: "started", Operation: operation, Method: r.Method, ObjectURI: object, RequestURI: uri, RequestSHA: hex.EncodeToString(checksum[:]), AuthRedacted: redacted, StartedNS: time.Now().UnixNano(), RequestContentLength: r.ContentLength, ResponseContentLength: -1}
		value.EventNS = value.StartedNS
		value.JournalFile = filepath.ToSlash(filepath.Join(filepath.Base(g.root), "requests", value.ID, "started.json"))
		if _, err := immutable(filepath.Join(directory, "started.json"), value); err != nil {
			g.failedJournal(w, err)
			return
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), 60*time.Second)
	stop := context.AfterFunc(g.stop, cancel)
	defer func() { stop(); cancel() }()
	out := r.Clone(ctx)
	out.URL.Scheme, out.URL.Host, out.RequestURI = "http", "storage:8333", ""
	out.Host = r.Host // SigV4 signs the incoming authority, not the dial target.
	out.Header = r.Header.Clone()
	out.Trailer = r.Trailer
	removeHopHeaders(out.Header)
	if _, ok := out.Header["User-Agent"]; !ok {
		out.Header["User-Agent"] = nil // Do not invent a signed/request header.
	}
	var body *countedBody
	// Wrapping NoBody makes Transport reframe an empty signed POST as chunked.
	if r.Body != nil && r.Body != http.NoBody {
		body = &countedBody{ReadCloser: r.Body}
		out.Body = body
	}
	response, err := g.transport.RoundTrip(out)
	if err != nil {
		if directory != "" {
			value.Event, value.EventNS, value.Error = "backend-error", time.Now().UnixNano(), "local upstream request failed or was canceled"
			value.JournalFile = filepath.ToSlash(filepath.Join(filepath.Base(g.root), "requests", value.ID, "backend-error.json"))
			if body != nil {
				value.StreamedRequestBytes = body.bytes.Load()
			}
			if _, journalErr := immutable(filepath.Join(directory, "backend-error.json"), value); journalErr != nil {
				g.failedJournal(w, journalErr)
				return
			}
		}
		http.Error(w, "local upstream request failed", http.StatusBadGateway)
		return
	}
	defer response.Body.Close()
	removeHopHeaders(response.Header)
	if operation == "" {
		for key, values := range response.Header {
			w.Header()[key] = append([]string(nil), values...)
		}
		for key := range response.Trailer {
			w.Header().Add("Trailer", key)
		}
		w.WriteHeader(response.StatusCode)
		if _, err := io.Copy(w, response.Body); err != nil {
			panic(http.ErrAbortHandler)
		}
		for key, values := range response.Trailer {
			w.Header()[http.TrailerPrefix+key] = append([]string(nil), values...)
		}
		return
	}
	responseBody, readErr := io.ReadAll(io.LimitReader(response.Body, (1<<20)+1))
	readErr = errors.Join(readErr, response.Body.Close())
	cancel()
	stop()
	value.BackendCompletedNS, value.EventNS = time.Now().UnixNano(), time.Now().UnixNano()
	value.BackendStatus, value.ResponseContentLength = response.StatusCode, response.ContentLength
	if body != nil {
		value.StreamedRequestBytes = body.bytes.Load()
	}
	success := response.StatusCode >= 200 && response.StatusCode < 300 && readErr == nil && len(responseBody) <= 1<<20
	if success && operation == "complete-multipart-upload" {
		var root struct{ XMLName xml.Name }
		success = xml.Unmarshal(responseBody, &root) == nil && root.XMLName.Local == "CompleteMultipartUploadResult"
	}
	hold := success && g.cfg.HoldFinalACKs
	value.Event, value.AckHeld = "backend-complete-error", hold
	if success {
		value.Event = "backend-complete-ack-forwarded"
		if hold {
			value.Event = "backend-complete-ack-held"
		}
	} else {
		value.Error = "upstream status/body did not establish successful final upload"
	}
	value.JournalFile = filepath.ToSlash(filepath.Join(filepath.Base(g.root), "requests", value.ID, "backend-complete.json"))
	if _, err := immutable(filepath.Join(directory, "backend-complete.json"), value); err != nil {
		g.failedJournal(w, err)
		return
	}
	g.mu.Lock()
	g.completed = append(g.completed, value)
	if hold {
		g.held[value.ID] = value
	}
	g.mu.Unlock()
	if !hold {
		if readErr != nil || len(responseBody) > 1<<20 || response.StatusCode >= 200 && response.StatusCode < 300 && !success {
			http.Error(w, "upstream response did not establish a valid final upload", http.StatusBadGateway)
			return
		}
		for key, values := range response.Header {
			w.Header()[key] = append([]string(nil), values...)
		}
		w.WriteHeader(response.StatusCode)
		_, _ = w.Write(responseBody)
		return
	}
	select {
	case <-r.Context().Done():
		value.Error = "incoming request canceled; acknowledgement never released"
	case <-g.stop.Done():
		value.Error = "gate stopped; acknowledgement never released"
	}
	value.Event, value.EventNS, value.AckHeld = "aborted", time.Now().UnixNano(), false
	value.JournalFile = filepath.ToSlash(filepath.Join(filepath.Base(g.root), "requests", value.ID, "aborted.json"))
	_, journalErr := immutable(filepath.Join(directory, "aborted.json"), value)
	g.mu.Lock()
	delete(g.held, value.ID)
	if journalErr != nil {
		g.fatal = "gate abort journal failed: " + journalErr.Error()
	}
	g.mu.Unlock()
	// Returning normally would make net/http emit an implicit empty 200.
	panic(http.ErrAbortHandler)
}

func (g *gate) admin() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet || r.URL.Path != "/status" {
			http.NotFound(w, r)
			return
		}
		g.mu.Lock()
		active := make([]record, 0, len(g.held))
		for _, value := range g.held {
			active = append(active, value)
		}
		sort.Slice(active, func(i, j int) bool { return active[i].ID < active[j].ID })
		completed := append([]record{}, g.completed...)
		fatal := g.fatal
		g.mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"version": 1, "run_id": g.cfg.RunID, "config_sha256": g.configSHA, "object_prefix": g.cfg.Prefix, "hold_final_acks": g.cfg.HoldFinalACKs, "active_holds": active, "completed": completed, "fatal_error": fatal, "stopping": g.stop.Err() != nil, "scope": "Transport lengths are not object size/completeness. active_holds are published only after the immutable backend-complete journal is fsynced. Held acknowledgements are never released on cancellation or shutdown. Forwarded-mode completion records authorize forwarding after journal fsync; client receipt requires its own successful result."})
	})
}
