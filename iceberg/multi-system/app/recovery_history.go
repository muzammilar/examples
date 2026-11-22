package main

import (
	"bufio"
	"crypto/sha256"
	"database/sql"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"hash"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

// Recovery tables and journals belong to one explicit UUID. Old runs are never
// dropped, truncated, or treated as the input to a different run.
var recoveryUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

const recoveryReferenceClient = "one connection per operation; application retries=0; http_retries=0; fresh reader attachment"

type recoveryConfig struct {
	Version              int    `json:"version"`
	RunID                string `json:"run_id"`
	Table                string `json:"table"`
	Rows                 int64  `json:"seed_rows"`
	Customers            int64  `json:"customers"`
	Batch                int64  `json:"batch_rows"`
	Writers              int    `json:"writers"`
	Readers              int    `json:"readers"`
	TimeoutSeconds       int64  `json:"operation_timeout_seconds"`
	VerifyTimeoutSeconds int64  `json:"verification_timeout_seconds"`
	DelayMillis          int64  `json:"operation_delay_ms"`
	VisibilitySecs       int64  `json:"visibility_bound_seconds"`
	CreatedNS            int64  `json:"created_ns"`
	Catalog              string `json:"catalog_url"`
	S3                   string `json:"s3_endpoint"`
	ReferenceClient      string `json:"reference_client"`
}

func (c recoveryConfig) validate() error {
	if c.Version != 1 || !recoveryUUID.MatchString(c.RunID) || c.Table != "recovery_"+strings.ReplaceAll(c.RunID, "-", "") ||
		c.Rows < 1 || c.Rows > 100000000 || c.Customers < 1 || c.Customers > 100000000 || c.Batch < 1 || c.Batch > 1000000 ||
		c.Writers < 1 || c.Writers > 16 || c.Readers < 1 || c.Readers > 16 || c.TimeoutSeconds < 1 || c.TimeoutSeconds > 300 ||
		c.VerifyTimeoutSeconds < 1 || c.VerifyTimeoutSeconds > 1800 ||
		c.DelayMillis < 0 || c.DelayMillis > 60000 || c.VisibilitySecs < 1 || c.VisibilitySecs > 3600 || c.CreatedNS < 1 ||
		c.Catalog != "http://storage:8181" || c.S3 != "storage:8333" || c.ReferenceClient != recoveryReferenceClient {
		return errors.New("invalid or non-local recovery config")
	}
	return nil
}

func recoveryDigest(data []byte) string { h := sha256.Sum256(data); return hex.EncodeToString(h[:]) }

func recoveryRoot() (string, error) {
	id := os.Getenv("RECOVERY_RUN_ID")
	if !recoveryUUID.MatchString(id) {
		return "", errors.New("RECOVERY_RUN_ID must be an explicit lowercase version-4 UUID")
	}
	base := os.Getenv("RESULTS_DIR")
	if base == "" {
		base = "/results"
	}
	return filepath.Join(base, "recovery-"+id), nil
}

func recoveryLoad(root string) (recoveryConfig, string, error) {
	var c recoveryConfig
	data, err := os.ReadFile(filepath.Join(root, "config.json"))
	if err != nil {
		return c, "", err
	}
	if err = json.Unmarshal(data, &c); err != nil {
		return c, "", err
	}
	if err = c.validate(); err != nil {
		return c, "", err
	}
	if filepath.Base(root) != "recovery-"+c.RunID {
		return c, "", errors.New("config/run directory identity mismatch")
	}
	return c, recoveryDigest(data), nil
}

func recoverySave(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0644)
	if err != nil {
		return err
	}
	_, err = f.Write(append(data, '\n'))
	if err == nil {
		err = f.Sync()
	}
	closeErr := f.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return recoverySyncDir(filepath.Dir(path))
}

func recoverySyncDir(path string) error {
	d, err := os.Open(path)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

type recoveryEvent struct {
	Kind         string `json:"kind"`
	RunID        string `json:"run_id"`
	ConfigHash   string `json:"config_sha256"`
	Actor        string `json:"actor"`
	ID           string `json:"operation_id"`
	Writer       int    `json:"writer"`
	Sequence     int64  `json:"sequence"`
	Lo           int64  `json:"first_id"`
	Hi           int64  `json:"end_id"`
	StartedNS    int64  `json:"started_ns"`
	EndedNS      int64  `json:"ended_ns,omitempty"`
	DurationNS   int64  `json:"duration_ns,omitempty"`
	Result       string `json:"result,omitempty"`
	Error        string `json:"error,omitempty"`
	Rows         int64  `json:"rows,omitempty"`
	BodyHash     string `json:"body_sha256,omitempty"`
	Locality     string `json:"locality,omitempty"`
	Engine       string `json:"engine,omitempty"`
	AckNS        int64  `json:"ack_ns,omitempty"`
	ReadSequence int64  `json:"read_sequence,omitempty"`
	Phase        string `json:"phase"`
	EndedPhase   string `json:"ended_phase,omitempty"`
}

type recoveryJournal struct{ file *os.File }

func recoveryOpenJournal(root, name string) (*recoveryJournal, error) {
	f, err := os.OpenFile(filepath.Join(root, name+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	// Persist the directory entry before the first request can be submitted.
	d, err := os.Open(root)
	if err == nil {
		err = d.Sync()
		_ = d.Close()
	}
	if err != nil {
		_ = f.Close()
		return nil, err
	}
	return &recoveryJournal{file: f}, nil
}

func (j *recoveryJournal) emit(e any) error {
	data, err := json.Marshal(e)
	if err != nil {
		return err
	}
	if _, err = j.file.Write(append(data, '\n')); err != nil {
		return err
	}
	return j.file.Sync()
}

// Only a final unterminated line can be ignored while a producer is live.
// Final verification is always strict, including an otherwise valid last JSON.
func recoveryEvents(path string, live bool) ([]recoveryEvent, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	r := bufio.NewReader(f)
	var out []recoveryEvent
	for {
		line, readErr := r.ReadBytes('\n')
		if len(line) == 0 && readErr == io.EOF {
			return out, nil
		}
		if readErr == io.EOF {
			if live {
				return out, nil
			}
			return nil, fmt.Errorf("unterminated final journal line in %s", path)
		}
		if readErr != nil {
			return nil, readErr
		}
		if len(line) > 1<<20 {
			return nil, errors.New("oversized operation journal line")
		}
		var e recoveryEvent
		if err = json.Unmarshal(line, &e); err != nil {
			return nil, fmt.Errorf("%s: %w", path, err)
		}
		out = append(out, e)
	}
}

type recoveryAttempt struct {
	Intent  recoveryEvent
	Outcome *recoveryEvent
}

func recoveryBounds(c recoveryConfig, writer int, seq int64) (int64, int64, error) {
	if seq < 0 || seq > 1000000 || writer < -1 || writer >= c.Writers {
		return 0, 0, errors.New("invalid writer/sequence")
	}
	if writer == -1 {
		lo := seq * c.Batch
		if lo >= c.Rows {
			return 0, 0, errors.New("seed sequence out of range")
		}
		return lo, min(lo+c.Batch, c.Rows), nil
	}
	lo := c.Rows + (seq*int64(c.Writers)+int64(writer))*c.Batch
	return lo, lo + c.Batch, nil
}

func recoveryAttemptEvent(c recoveryConfig, digest string, writer int, seq int64) (recoveryEvent, error) {
	lo, hi, err := recoveryBounds(c, writer, seq)
	actor := fmt.Sprintf("writer-%03d", writer)
	if writer == -1 {
		actor = "seed"
	}
	return recoveryEvent{Kind: "intent", RunID: c.RunID, ConfigHash: digest, Actor: actor, ID: fmt.Sprintf("%s:%s:%d", c.RunID, actor, seq), Writer: writer, Sequence: seq, Lo: lo, Hi: hi, StartedNS: time.Now().UnixNano()}, err
}

func recoveryReadAttempts(root string, c recoveryConfig, digest string, live bool) ([]recoveryAttempt, error) {
	var result []recoveryAttempt
	for writer := -1; writer < c.Writers; writer++ {
		name := fmt.Sprintf("writer-%03d", writer)
		if writer == -1 {
			name = "seed"
		}
		path := filepath.Join(root, name+".jsonl")
		events, err := recoveryEvents(path, live)
		if live && os.IsNotExist(err) && writer >= 0 {
			continue
		}
		if err != nil {
			return nil, err
		}
		var pending *recoveryAttempt
		seq := int64(0)
		for _, e := range events {
			expected, err := recoveryAttemptEvent(c, digest, writer, e.Sequence)
			if err != nil || e.RunID != c.RunID || e.ConfigHash != digest || e.Actor != name || e.Writer != writer || e.ID != expected.ID || e.Lo != expected.Lo || e.Hi != expected.Hi || e.StartedNS < c.CreatedNS {
				return nil, fmt.Errorf("invalid or cross-run history in %s", path)
			}
			switch e.Kind {
			case "intent":
				if pending != nil || e.Sequence != seq || e.Result != "" {
					return nil, errors.New("duplicate, out-of-order, or overlapping intent")
				}
				result = append(result, recoveryAttempt{Intent: e})
				pending = &result[len(result)-1]
			case "outcome":
				if pending == nil || e.Sequence != seq || e.StartedNS != pending.Intent.StartedNS || e.EndedNS < e.StartedNS || e.DurationNS < 0 || (e.Result != "ok" && e.Result != "fail" && e.Result != "ambiguous") || (e.Result == "ok" && e.Error != "") || (e.Result != "ok" && e.Error == "") {
					return nil, errors.New("unmatched or invalid outcome")
				}
				copy := e
				pending.Outcome = &copy
				pending = nil
				seq++
			default:
				return nil, errors.New("unknown mutation event kind")
			}
		}
		if !live && writer == -1 && (pending != nil || seq != (c.Rows+c.Batch-1)/c.Batch) {
			return nil, errors.New("complete seeded history required")
		}
		// A fully fsynced intent without a response is an ambiguous attempt,
		// including a workload process crash. It is never counted as an ACK.
	}
	sort.Slice(result, func(i, j int) bool { return result[i].Intent.Lo < result[j].Intent.Lo })
	return result, nil
}

type recoveryRow struct {
	ID, Customer, Amount, Writer, Lo, Hi int64
	Payload                              string
}

// The native ClickHouse driver requires its exact numeric width or sql.Scanner;
// database/sql's standard scanners handle both Iceberg INTEGER and BIGINT.
// Check NULL explicitly so NULL customer zero cannot look like a valid body.
func recoveryScan(rows interface{ Scan(...any) error }) (recoveryRow, error) {
	var row recoveryRow
	var values [6]sql.NullInt64
	var payload sql.NullString
	if err := rows.Scan(&values[0], &values[1], &values[2], &values[3], &values[4], &values[5], &payload); err != nil {
		return row, err
	}
	for _, v := range values {
		if !v.Valid {
			return row, errors.New("NULL in deterministic recovery body")
		}
	}
	if !payload.Valid {
		return row, errors.New("NULL recovery payload hash")
	}
	row = recoveryRow{ID: values[0].Int64, Customer: values[1].Int64, Amount: values[2].Int64, Writer: values[3].Int64, Lo: values[4].Int64, Hi: values[5].Int64, Payload: payload.String}
	return row, nil
}

var recoveryColumns = []string{"event_id", "customer_id", "amount_cents", "writer_id", "batch_start", "batch_end", "payload_sha256"}

func recoveryExpected(c recoveryConfig, e recoveryEvent, id int64) recoveryRow {
	return recoveryRow{ID: id, Customer: id % c.Customers, Amount: id%10000 + 1, Writer: int64(e.Writer), Lo: e.Lo, Hi: e.Hi, Payload: recoveryDigest([]byte(strconv.FormatInt(id, 10)))}
}
func (r recoveryRow) fields() []string {
	return []string{strconv.FormatInt(r.ID, 10), strconv.FormatInt(r.Customer, 10), strconv.FormatInt(r.Amount, 10), strconv.FormatInt(r.Writer, 10), strconv.FormatInt(r.Lo, 10), strconv.FormatInt(r.Hi, 10), r.Payload}
}

func recoveryParseRow(fields []string) (recoveryRow, error) {
	var r recoveryRow
	if len(fields) != 7 {
		return r, errors.New("wrong recovery CSV field count")
	}
	values := []*int64{&r.ID, &r.Customer, &r.Amount, &r.Writer, &r.Lo, &r.Hi}
	for i, p := range values {
		v, err := strconv.ParseInt(fields[i], 10, 64)
		if err != nil {
			return r, err
		}
		*p = v
	}
	r.Payload = fields[6]
	return r, nil
}

type recoveryProof struct {
	Rows             int64               `json:"rows_verified"`
	AckBatches       int64               `json:"acknowledged_batches"`
	AckRows          int64               `json:"acknowledged_rows"`
	AmbiguousApplied int64               `json:"ambiguous_batches_applied"`
	AmbiguousAbsent  int64               `json:"ambiguous_batches_absent"`
	KnownFailed      int64               `json:"known_failed_batches_absent"`
	BodySHA256       string              `json:"canonical_body_sha256"`
	AmbiguousBatches []recoveryAmbiguity `json:"ambiguous_batches"`
}

type recoveryAmbiguity struct {
	ID      string `json:"operation_id"`
	Applied bool   `json:"applied"`
	Rows    int64  `json:"rows"`
}

// Streaming full-body oracle. Gaps are allowed only for completely absent
// unacknowledged attempts; duplicate, partial and unexpected application fails.
func recoveryReconcile(input io.Reader, c recoveryConfig, attempts []recoveryAttempt) (recoveryProof, error) {
	var proof recoveryProof
	r := csv.NewReader(input)
	header, err := r.Read()
	if err != nil {
		return proof, err
	}
	if strings.Join(header, ",") != strings.Join(recoveryColumns, ",") {
		return proof, errors.New("wrong CSV schema")
	}
	hash := sha256.New()
	canonical := csv.NewWriter(hash)
	var fields []string
	readNext := func() error { var err error; fields, err = r.Read(); return err }
	err = readNext()
	for _, attempt := range attempts {
		e := attempt.Intent
		present := int64(0)
		for err == nil {
			row, parseErr := recoveryParseRow(fields)
			if parseErr != nil {
				return proof, parseErr
			}
			if row.ID >= e.Hi {
				break
			}
			if row.ID != e.Lo+present || row != recoveryExpected(c, e, row.ID) {
				return proof, fmt.Errorf("wrong, duplicate or unexpected body at id %d (batch %s)", row.ID, e.ID)
			}
			if writeErr := canonical.Write(fields); writeErr != nil {
				return proof, writeErr
			}
			present++
			proof.Rows++
			err = readNext()
		}
		if err != nil && err != io.EOF {
			return proof, err
		}
		if present != 0 && present != e.Hi-e.Lo {
			return proof, fmt.Errorf("partial batch %s: %d/%d", e.ID, present, e.Hi-e.Lo)
		}
		result := "ambiguous"
		if attempt.Outcome != nil {
			result = attempt.Outcome.Result
		}
		switch result {
		case "ok":
			if present != e.Hi-e.Lo {
				return proof, fmt.Errorf("acknowledged batch missing: %s", e.ID)
			}
			proof.AckBatches++
			proof.AckRows += present
		case "fail":
			if present != 0 {
				return proof, fmt.Errorf("pre-submission failed batch unexpectedly applied: %s", e.ID)
			}
			proof.KnownFailed++
		case "ambiguous":
			proof.AmbiguousBatches = append(proof.AmbiguousBatches, recoveryAmbiguity{ID: e.ID, Applied: present != 0, Rows: present})
			if present == 0 {
				proof.AmbiguousAbsent++
			} else {
				proof.AmbiguousApplied++
			}
		default:
			return proof, errors.New("invalid outcome class")
		}
	}
	if err != io.EOF {
		return proof, errors.New("unexpected rows outside witnessed intents")
	}
	canonical.Flush()
	if err := canonical.Error(); err != nil {
		return proof, err
	}
	proof.BodySHA256 = hex.EncodeToString(hash.Sum(nil))
	return proof, nil
}

type recoveryBodyHash struct {
	hash hash.Hash
	csv  *csv.Writer
}

func newRecoveryBodyHash() *recoveryBodyHash {
	h := sha256.New()
	return &recoveryBodyHash{hash: h, csv: csv.NewWriter(h)}
}
func (h *recoveryBodyHash) write(row recoveryRow) error { return h.csv.Write(row.fields()) }
func (h *recoveryBodyHash) finish() string              { h.csv.Flush(); return hex.EncodeToString(h.hash.Sum(nil)) }

func recoveryVerifyReaders(root string, c recoveryConfig, digest string, attempts []recoveryAttempt) error {
	acks := map[string]recoveryEvent{}
	for _, a := range attempts {
		if a.Outcome != nil && a.Outcome.Result == "ok" {
			acks[a.Intent.ID] = *a.Outcome
		}
	}
	for id := 0; id < c.Readers; id++ {
		actor := fmt.Sprintf("reader-%03d", id)
		events, err := recoveryEvents(filepath.Join(root, actor+".jsonl"), false)
		if err != nil {
			return err
		}
		observed := map[string]bool{}
		for seq, e := range events {
			ack, exists := acks[e.ID]
			if !exists || e.Kind != "read" || e.RunID != c.RunID || e.ConfigHash != digest || e.Actor != actor || e.ReadSequence != int64(seq) || e.Lo != ack.Lo || e.Hi != ack.Hi || e.Writer != ack.Writer || e.Sequence != ack.Sequence || e.AckNS != ack.EndedNS || e.StartedNS < ack.EndedNS || e.EndedNS < e.StartedNS || e.DurationNS < 0 || (e.Engine != "duckdb" && e.Engine != "clickhouse") || (e.Locality != "recent" && e.Locality != "historical") {
				return errors.New("invalid, unwitnessed or cross-run reader observation")
			}
			switch e.Result {
			case "ok":
				h := newRecoveryBodyHash()
				for i := e.Lo; i < e.Hi; i++ {
					if err := h.write(recoveryExpected(c, ack, i)); err != nil {
						return err
					}
				}
				if e.Rows != e.Hi-e.Lo || e.BodyHash != h.finish() || e.Error != "" {
					return errors.New("reader success body hash differs from independent oracle")
				}
				observed[e.Locality] = true
			case "stale":
				if e.Rows != 0 || e.Error != "" || e.EndedNS-e.AckNS > int64(time.Duration(c.VisibilitySecs)*time.Second) {
					return errors.New("invalid or out-of-bound complete-snapshot staleness observation")
				}
			case "visibility_timeout":
				return errors.New("declared fresh-reader visibility bound exceeded")
			case "fail":
				if e.Error == "" {
					return errors.New("reader failure without diagnostic")
				}
			default:
				return fmt.Errorf("reader integrity violation: %s", e.Result)
			}
		}
		if !observed["historical"] || !observed["recent"] {
			return fmt.Errorf("reader %s lacks successful historical and recent batch observations", actor)
		}
	}
	return nil
}

func recoveryFileHash(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func recoveryWitnessHashes(root string) (map[string]string, error) {
	paths, err := filepath.Glob(filepath.Join(root, "*.jsonl"))
	if err != nil {
		return nil, err
	}
	paths = append(paths, filepath.Join(root, "config.json"), filepath.Join(root, "seed-complete.json"))
	result := map[string]string{}
	for _, path := range paths {
		digest, err := recoveryFileHash(path)
		if err != nil {
			return nil, err
		}
		result[filepath.Base(path)] = digest
	}
	return result, nil
}

func recoveryVerificationDir(root, id string) (string, error) {
	if !recoveryUUID.MatchString(id) {
		return "", errors.New("RECOVERY_VERIFICATION_ID must be an explicit lowercase version-4 UUID")
	}
	info, err := os.Lstat(root)
	if err != nil {
		return "", err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("regular recovery run directory required")
	}
	parent := filepath.Join(root, "verifications")
	if err = os.Mkdir(parent, 0755); err != nil && !os.IsExist(err) {
		return "", err
	}
	info, err = os.Lstat(parent)
	if err != nil {
		return "", err
	}
	if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", errors.New("verification parent must be a regular owned directory")
	}
	if err = recoverySyncDir(root); err != nil {
		return "", err
	}
	path := filepath.Join(parent, id)
	if err = os.Mkdir(path, 0755); err != nil {
		return "", fmt.Errorf("fresh verification UUID required; preserve earlier exports: %w", err)
	}
	if err = recoverySyncDir(parent); err != nil {
		return "", err
	}
	return path, nil
}

func recoveryActorIssues(root string, c recoveryConfig, digest string) []string {
	var issues []string
	for _, kind := range []string{"writer", "reader"} {
		count := c.Writers
		if kind == "reader" {
			count = c.Readers
		}
		for id := 0; id < count; id++ {
			actor := fmt.Sprintf("%s-%03d", kind, id)
			data, err := os.ReadFile(filepath.Join(root, actor+"-ended.json"))
			if err != nil {
				issues = append(issues, actor+": no durable clean completion: "+err.Error())
				continue
			}
			var ended struct {
				RunID  string `json:"run_id"`
				Config string `json:"config_sha256"`
				Clean  bool   `json:"clean_shutdown"`
			}
			if err = json.Unmarshal(data, &ended); err != nil {
				issues = append(issues, actor+": malformed completion: "+err.Error())
				continue
			}
			if ended.RunID != c.RunID || ended.Config != digest || !ended.Clean {
				issues = append(issues, actor+": completion identity/clean-shutdown mismatch")
			}
		}
	}
	return issues
}

func recoveryRequireQualification(issues []string, diagnostic bool) error {
	if len(issues) > 0 && !diagnostic {
		return fmt.Errorf("actor/history qualification failed: %s", strings.Join(issues, "; "))
	}
	return nil
}

// Read lifetime counters inside the actor before exit; a host sampler can miss
// short memory peaks. These are cumulative counters, not isolated engine costs.
func recoveryNativeCounters() map[string]any {
	files := map[string]any{}
	for _, path := range []string{
		"/sys/fs/cgroup/cpu.stat", "/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/io.stat",
		"/proc/self/io", "/proc/self/status",
	} {
		data, err := os.ReadFile(path)
		if err != nil {
			files[path] = map[string]string{"unavailable_error": err.Error()}
		} else {
			files[path] = map[string]string{"raw": string(data)}
		}
	}
	return map[string]any{"utc_ns": time.Now().UnixNano(), "files": files, "scope": "Raw cumulative process/container counters. Cgroup peaks and totals can include earlier work in this container; unavailable counters are not zero."}
}
