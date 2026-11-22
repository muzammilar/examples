package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

const testRun = "36e77aab-4a09-4bf8-8192-2b4ed20a7696"
const testPrefix = "s3://warehouse/lab/recovery_36e77aab4a094bf881922b4ed20a7696-21870514-0f8c-4195-a836-dd83cf40bce7/data/"

func fixture(t *testing.T, handler http.HandlerFunc) (*gate, *httptest.Server, context.CancelFunc) {
	t.Helper()
	return fixtureMode(t, handler, true)
}

func fixtureMode(t *testing.T, handler http.HandlerFunc, hold bool) (*gate, *httptest.Server, context.CancelFunc) {
	t.Helper()
	backend := httptest.NewServer(handler)
	transport := localTransport()
	transport.DialContext = func(ctx context.Context, network, address string) (net.Conn, error) {
		if address != "storage:8333" {
			t.Errorf("unexpected upstream destination %s", address)
		}
		return (&net.Dialer{}).DialContext(ctx, network, backend.Listener.Addr().String())
	}
	ctx, cancel := context.WithCancel(context.Background())
	g, err := newGate(ctx, config{RunID: testRun, Prefix: testPrefix, Results: t.TempDir(), HoldFinalACKs: hold}, transport)
	if err != nil {
		t.Fatal(err)
	}
	front := httptest.NewServer(g)
	t.Cleanup(func() { cancel(); front.Close(); backend.Close(); transport.CloseIdleConnections() })
	return g, front, cancel
}

func active(t *testing.T, g *gate) []record {
	t.Helper()
	w := httptest.NewRecorder()
	g.admin().ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/status", nil))
	var status struct {
		RunID       string   `json:"run_id"`
		ConfigSHA   string   `json:"config_sha256"`
		Prefix      string   `json:"object_prefix"`
		Fatal       string   `json:"fatal_error"`
		ActiveHolds []record `json:"active_holds"`
	}
	if err := json.Unmarshal(w.Body.Bytes(), &status); err != nil || status.Fatal != "" || status.RunID != testRun || status.ConfigSHA != g.configSHA || status.Prefix != testPrefix {
		t.Fatalf("bad admin status %s: %v", w.Body.String(), err)
	}
	return status.ActiveHolds
}

func awaitHolds(t *testing.T, g *gate, count int) []record {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if values := active(t, g); len(values) == count {
			return values
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatalf("did not observe %d held acknowledgements", count)
	return nil
}

type outcome struct {
	response *http.Response
	err      error
}

func pending(t *testing.T, front *httptest.Server, method, suffix, body string) (<-chan outcome, context.CancelFunc) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	path := "/warehouse/" + strings.TrimPrefix(testPrefix, "s3://warehouse/") + suffix
	r, err := http.NewRequestWithContext(ctx, method, front.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	r.Host = "signed-gate:8333"
	r.Header.Set("Authorization", "never-log-this-authorization")
	r.Header.Set("X-Amz-Date", "20261122T120000Z")
	result := make(chan outcome, 1)
	go func() { response, err := front.Client().Do(r); result <- outcome{response, err} }()
	return result, cancel
}

func awaitAborted(t *testing.T, g *gate, id string) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		body, err := os.ReadFile(filepath.Join(g.root, "requests", id, "aborted.json"))
		if err == nil {
			var value record
			if json.Unmarshal(body, &value) == nil && value.Event == "aborted" && !value.AckHeld && strings.Contains(value.Error, "never released") {
				return
			}
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("abort event missing")
}

func TestConfigRequiresExactOwnedPrefix(t *testing.T) {
	env := map[string]string{"GATE_RUN_ID": testRun, "GATE_OBJECT_PREFIX": testPrefix}
	if _, err := loadConfig(func(key string) string { return env[key] }); err != nil {
		t.Fatal(err)
	}
	for _, prefix := range []string{"s3://warehouse/", strings.Replace(testPrefix, "warehouse", "other", 1), strings.Replace(testPrefix, "36e77aab4", "46e77aab4", 1), testPrefix + "../", strings.Replace(testPrefix, "/data/", "/%64ata/", 1), testPrefix + "?secret=x"} {
		env["GATE_OBJECT_PREFIX"] = prefix
		if _, err := loadConfig(func(key string) string { return env[key] }); err == nil {
			t.Errorf("accepted foreign/broad/noncanonical prefix %q", prefix)
		}
	}
	env["GATE_OBJECT_PREFIX"], env["GATE_RUN_ID"] = testPrefix, strings.ToUpper(testRun)
	if _, err := loadConfig(func(key string) string { return env[key] }); err == nil {
		t.Fatal("accepted noncanonical run ID")
	}
	env["GATE_RUN_ID"] = testRun
	for _, invalid := range []string{"1", "yes", "TRUE", "false "} {
		env["GATE_HOLD_FINAL_ACKS"] = invalid
		if _, err := loadConfig(func(key string) string { return env[key] }); err == nil {
			t.Errorf("accepted non-strict boolean %q", invalid)
		}
	}
}

func TestBaselineForwardingModeIsImmutableAndStillJournaled(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		w.Header().Set("ETag", "native-etag")
	}))
	defer backend.Close()
	transport := localTransport()
	defer transport.CloseIdleConnections()
	transport.DialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, network, backend.Listener.Addr().String())
	}
	g, err := newGate(context.Background(), config{RunID: testRun, Prefix: testPrefix, Results: t.TempDir(), HoldFinalACKs: false}, transport)
	if err != nil {
		t.Fatal(err)
	}
	front := httptest.NewServer(g)
	defer front.Close()
	result, cancel := pending(t, front, http.MethodPut, "baseline.parquet", "data")
	defer cancel()
	answer := <-result
	if answer.err != nil || answer.response.StatusCode != 200 || answer.response.Header.Get("ETag") != "native-etag" {
		t.Fatalf("baseline response changed: %+v", answer)
	}
	answer.response.Body.Close()
	if len(active(t, g)) != 0 || len(g.completed) != 1 || g.completed[0].Event != "backend-complete-ack-forwarded" || g.completed[0].AckHeld {
		t.Fatal("baseline was held or lacked a completion journal")
	}
	var cfg map[string]any
	data, err := os.ReadFile(filepath.Join(g.root, "config.json"))
	if err != nil || json.Unmarshal(data, &cfg) != nil || cfg["hold_final_acks"] != false {
		t.Fatal("baseline policy was not frozen in the immutable config")
	}
}

func TestGateRunCannotReuseOrChangePolicy(t *testing.T) {
	results := t.TempDir()
	cfg := config{RunID: testRun, Prefix: testPrefix, Results: results, HoldFinalACKs: true}
	g, err := newGate(context.Background(), cfg, localTransport())
	if err != nil {
		t.Fatal(err)
	}
	original, err := os.ReadFile(filepath.Join(g.root, "config.json"))
	if err != nil {
		t.Fatal(err)
	}
	cfg.HoldFinalACKs = false
	if _, err := newGate(context.Background(), cfg, localTransport()); err == nil {
		t.Fatal("existing gate/config was reused or rearmed")
	}
	current, err := os.ReadFile(filepath.Join(g.root, "config.json"))
	if err != nil || string(current) != string(original) {
		t.Fatal("existing gate configuration changed")
	}
	cfg.Results = t.TempDir() // A different invocation has a different owned root.
	if baseline, err := newGate(context.Background(), cfg, localTransport()); err != nil || filepath.Base(baseline.root) != "upload-gate-"+testRun {
		t.Fatalf("independent baseline invocation failed: %v", err)
	}
}

func TestFinalUploadsPreserveSignedWireAndAreDurablyHeld(t *testing.T) {
	for _, method := range []string{http.MethodPut, http.MethodPost} {
		t.Run(method, func(t *testing.T) {
			body := "binary\x00data"
			suffix := "part%2B%20file.parquet?custom=a%2Fb&x-id=PutObject"
			if method == http.MethodPost {
				body, suffix = "<CompleteMultipartUpload><Part/></CompleteMultipartUpload>", "part%2B%20file.parquet?uploadId=u%2B123&custom=a%2Fb"
			}
			g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
				data, err := io.ReadAll(r.Body)
				if err != nil || string(data) != body || r.Host != "signed-gate:8333" || r.Header.Get("Authorization") != "never-log-this-authorization" || r.Header.Get("X-Amz-Date") != "20261122T120000Z" || r.RequestURI != "/warehouse/"+strings.TrimPrefix(testPrefix, "s3://warehouse/")+suffix {
					t.Errorf("signed wire changed: %s %s %s %q %v", r.Method, r.Host, r.RequestURI, data, err)
				}
				if method == http.MethodPost {
					io.WriteString(w, `<CompleteMultipartUploadResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><ETag>etag</ETag></CompleteMultipartUploadResult>`)
				}
			})
			result, cancel := pending(t, front, method, suffix, body)
			defer cancel()
			values := awaitHolds(t, g, 1)
			value := values[0]
			if value.Event != "backend-complete-ack-held" || !value.AckHeld || value.BackendStatus != 200 || value.StreamedRequestBytes != int64(len(body)) || value.RequestContentLength != int64(len(body)) || value.BackendCompletedNS < value.StartedNS || value.ObjectURI != testPrefix+"part+%20file.parquet" {
				t.Fatalf("bad held upload evidence: %+v", value)
			}
			data, err := os.ReadFile(filepath.Join(g.cfg.Results, filepath.FromSlash(value.JournalFile)))
			var persisted record
			if err != nil || json.Unmarshal(data, &persisted) != nil || !reflect.DeepEqual(value, persisted) || strings.Contains(string(data), "never-log-this-authorization") {
				t.Fatalf("active hold precedes matching durable journal: %s %v", data, err)
			}
			select {
			case answer := <-result:
				t.Fatalf("success acknowledgement escaped gate: %+v", answer)
			default:
			}
			cancel()
			awaitAborted(t, g, value.ID)
			awaitHolds(t, g, 0)
		})
	}
}

func TestAllConcurrentRetryAcknowledgementsAreHeld(t *testing.T) {
	var calls atomic.Int32
	g, front, stop := fixture(t, func(w http.ResponseWriter, r *http.Request) { io.Copy(io.Discard, r.Body); calls.Add(1) })
	first, cancelFirst := pending(t, front, http.MethodPut, "retry.parquet", "data")
	defer cancelFirst()
	second, cancelSecond := pending(t, front, http.MethodPut, "retry.parquet", "data")
	defer cancelSecond()
	values := awaitHolds(t, g, 2)
	if calls.Load() != 2 || values[0].ID == values[1].ID {
		t.Fatal("retry reused a journal or was not forwarded independently")
	}
	stop()
	for _, result := range []<-chan outcome{first, second} {
		select {
		case answer := <-result:
			if answer.err == nil || answer.response != nil {
				t.Fatalf("controlled stop emitted a success response: %+v", answer)
			}
		case <-time.After(3 * time.Second):
			t.Fatal("controlled stop did not abort pending acknowledgement")
		}
	}
	for _, value := range values {
		awaitAborted(t, g, value.ID)
	}
}

func TestUploadBodyStreamsBeforeClientEOF(t *testing.T) {
	firstReceived := make(chan struct{})
	g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		prefix := make([]byte, 5)
		if _, err := io.ReadFull(r.Body, prefix); err != nil || string(prefix) != "first" {
			t.Errorf("upstream prefix: %q %v", prefix, err)
		}
		close(firstReceived)
		rest, err := io.ReadAll(r.Body)
		if err != nil || string(rest) != "remaining" {
			t.Errorf("upstream remaining body: %q %v", rest, err)
		}
	})
	reader, writer := io.Pipe()
	defer reader.Close()
	defer writer.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request, err := http.NewRequestWithContext(ctx, http.MethodPut, front.URL+"/warehouse/"+strings.TrimPrefix(testPrefix, "s3://warehouse/")+"stream.parquet", reader)
	if err != nil {
		t.Fatal(err)
	}
	result := make(chan outcome, 1)
	go func() { response, err := front.Client().Do(request); result <- outcome{response, err} }()
	if _, err := writer.Write([]byte("first")); err != nil {
		t.Fatal(err)
	}
	select {
	case <-firstReceived:
	case <-time.After(3 * time.Second):
		t.Fatal("proxy buffered the request instead of streaming it")
	}
	if _, err := writer.Write([]byte("remaining")); err != nil {
		t.Fatal(err)
	}
	writer.Close()
	values := awaitHolds(t, g, 1)
	if values[0].RequestContentLength != -1 || values[0].StreamedRequestBytes != 14 {
		t.Fatalf("unknown length was fabricated or decoded bytes changed: %+v", values[0])
	}
	cancel()
	awaitAborted(t, g, values[0].ID)
}

func TestUploadPartsAndNonTargetRequestsPassThrough(t *testing.T) {
	g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		w.Header().Set("X-Amz-Test", "unchanged")
		w.WriteHeader(201)
		w.Write(body)
	})
	for _, suffix := range []string{"part.parquet?partNumber=1&uploadId=abc", "part.parquet?acl", "metadata.avro", "../outside.parquet"} {
		path := "/warehouse/" + strings.TrimPrefix(testPrefix, "s3://warehouse/") + suffix
		if strings.HasPrefix(suffix, "../") {
			path = "/warehouse/not-owned/outside.parquet"
		}
		request, _ := http.NewRequest(http.MethodPut, front.URL+path, strings.NewReader("opaque-body"))
		response, err := front.Client().Do(request)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(response.Body)
		response.Body.Close()
		if response.StatusCode != 201 || string(body) != "opaque-body" || response.Header.Get("X-Amz-Test") != "unchanged" {
			t.Fatal("non-target wire response changed")
		}
	}
	if len(active(t, g)) != 0 {
		t.Fatal("upload part or unrelated object was held")
	}
}

func TestBackendFailuresCannotQualifyACompletedObject(t *testing.T) {
	for _, code := range []int{503, 200} {
		t.Run(http.StatusText(code), func(t *testing.T) {
			g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
				io.Copy(io.Discard, r.Body)
				w.WriteHeader(code)
				io.WriteString(w, `<Error><Code>InternalError</Code></Error>`)
			})
			result, cancel := pending(t, front, http.MethodPost, "data.parquet?uploadId=abc", "<CompleteMultipartUpload/>")
			defer cancel()
			select {
			case answer := <-result:
				if answer.err != nil {
					t.Fatal(answer.err)
				}
				answer.response.Body.Close()
			case <-time.After(3 * time.Second):
				t.Fatal("backend error was incorrectly held as success")
			}
			if len(active(t, g)) != 0 || len(g.completed) != 1 || g.completed[0].AckHeld || g.completed[0].Event != "backend-complete-error" {
				t.Fatal("backend failure qualified as completed object")
			}
		})
	}
}

func TestJournalFailureAfterBackendApplicationRefusesAcknowledgement(t *testing.T) {
	var g *gate
	var applied atomic.Int32
	g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		applied.Add(1)
		dirs, err := os.ReadDir(filepath.Join(g.root, "requests"))
		if err != nil || len(dirs) != 1 {
			t.Error("request intent was not reserved before upstream")
			return
		}
		// Deliberately collide with the exclusive completion file after application.
		if err := os.WriteFile(filepath.Join(g.root, "requests", dirs[0].Name(), "backend-complete.json"), []byte("preserve collision"), 0600); err != nil {
			t.Error(err)
		}
	})
	result, cancel := pending(t, front, http.MethodPut, "data.parquet", "applied-body")
	defer cancel()
	answer := <-result
	if answer.err != nil || answer.response.StatusCode != 503 || applied.Load() != 1 {
		t.Fatalf("journal failure did not refuse success: %+v", answer)
	}
	answer.response.Body.Close()
	g.mu.Lock()
	fatal, held := g.fatal, len(g.held)
	g.mu.Unlock()
	if fatal == "" || held != 0 {
		t.Fatal("journal failure left a qualifying gate")
	}
}

func TestAuthorizationQueryValuesAreNotJournaled(t *testing.T) {
	r := httptest.NewRequest(http.MethodPut, "/data.parquet?uploadId=u%2B1&X-Amz-Credential=private&X-Amz-Signature=secret&ordinary=a%2Fb", nil)
	value, redacted := safeRequestURI(r)
	if !redacted || strings.Contains(value, "private") || strings.Contains(value, "secret") || !strings.Contains(value, "uploadId=u%2B1") || !strings.Contains(value, "ordinary=a%2Fb") {
		t.Fatalf("authorization leaked or ordinary query changed: %s", value)
	}
}

func TestInitialJournalFailurePreventsUpstreamMutation(t *testing.T) {
	var calls atomic.Int32
	g, front, _ := fixture(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	if err := os.Remove(filepath.Join(g.root, "requests")); err != nil {
		t.Fatal(err)
	}
	result, cancel := pending(t, front, http.MethodPut, "data.parquet", "body")
	defer cancel()
	answer := <-result
	if answer.err != nil || answer.response.StatusCode != 503 || calls.Load() != 0 {
		t.Fatalf("unwitnessed mutation forwarded: %+v calls=%d", answer, calls.Load())
	}
	answer.response.Body.Close()
}
