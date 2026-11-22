package main

import (
	"context"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/ClickHouse/clickhouse-go/v2"
)

func recoveryContext(root string) (context.Context, context.CancelFunc) {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	go func() {
		ticker := time.NewTicker(200 * time.Millisecond)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				if _, err := os.Stat(filepath.Join(root, "STOP")); err == nil {
					cancel()
					return
				}
			}
		}
	}()
	return ctx, cancel
}

func recoveryWait(ctx context.Context, millis int64) bool {
	timer := time.NewTimer(time.Duration(millis) * time.Millisecond)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func recoveryPhase(root string) string {
	data, err := os.ReadFile(filepath.Join(root, "PHASE"))
	if os.IsNotExist(err) {
		return "unspecified"
	}
	if err != nil {
		return "unavailable"
	}
	if len(data) > 256 {
		return "invalid-oversized-phase"
	}
	return strings.TrimSpace(string(data))
}

// Every process owns its own in-memory DuckDB. Attaching a shared catalog does
// not share a writable .duckdb file. Setup is covered by the operation deadline.
func recoveryDuck(ctx context.Context, c recoveryConfig) (*sql.DB, error) {
	db, err := sql.Open("duckdb", "")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	var engine string
	if err = db.QueryRowContext(ctx, "SELECT version()").Scan(&engine); err == nil {
		_, err = qualifiedExtensionPins(engine, expectedNativeVersion)
	}
	if err != nil {
		_ = db.Close()
		return nil, err
	}
	for _, q := range []string{
		// Full sorted verification can exceed memory. Keep bounded spill inside
		// this disposable container; its default working directory is not writable.
		"SET threads=2", "SET memory_limit='512MB'", "SET temp_directory='/tmp/duckdb-recovery-spill'", "SET max_temp_directory_size='1GiB'", "SET extension_directory='/opt/duckdb/extensions'", "SET autoinstall_known_extensions=false", "SET allow_persistent_secrets=false", "LOAD httpfs", "LOAD iceberg",
		fmt.Sprintf("SET http_timeout=%d", c.TimeoutSeconds), "SET http_retries=0", "SET enable_global_s3_configuration=false",
		"CREATE SECRET recovery_s3 (TYPE S3,KEY_ID 'demo',SECRET 'demo',ENDPOINT 'storage:8333',URL_STYLE 'path',USE_SSL false)",
		"CREATE SECRET recovery_catalog (TYPE ICEBERG,ENDPOINT 'http://storage:8181',CLIENT_ID 'demo',CLIENT_SECRET 'demo')",
		"ATTACH 's3://warehouse' AS lake (TYPE iceberg,SECRET recovery_catalog,ENDPOINT 'http://storage:8181',READ_ONLY false)",
	} {
		if _, err = db.ExecContext(ctx, q); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	return db, nil
}

func recoveryInsert(c recoveryConfig, e recoveryEvent) string {
	return fmt.Sprintf("INSERT INTO lake.lab.%s SELECT i,CAST(i%%%d AS INTEGER),i%%10000+1,CAST(%d AS INTEGER),CAST(%d AS BIGINT),CAST(%d AS BIGINT),sha256(CAST(i AS VARCHAR)) FROM range(%d,%d) t(i)", c.Table, c.Customers, e.Writer, e.Lo, e.Hi, e.Lo, e.Hi)
}

func recoveryMutate(ctx context.Context, c recoveryConfig, j *recoveryJournal, e recoveryEvent) (recoveryEvent, error) {
	e.Phase = recoveryPhase(filepath.Dir(j.file.Name()))
	if err := j.emit(e); err != nil {
		return e, err
	} // Fsync intent before even connecting.
	started := time.Now()
	opctx, cancel := context.WithTimeout(ctx, time.Duration(c.TimeoutSeconds)*time.Second)
	defer cancel()
	db, err := recoveryDuck(opctx, c)
	result := "fail" // Setup failed before submitting the INSERT.
	var closeErr error
	if err == nil {
		result = "ambiguous"
		_, err = db.ExecContext(opctx, recoveryInsert(c, e))
		if err == nil {
			result = "ok"
		}
		closeErr = db.Close()
		// A successful INSERT stays acknowledged if closing its independent
		// connection fails. Keep the close diagnostic separate from the RPC.
		if closeErr != nil && err == nil {
			fmt.Fprintf(os.Stderr, "DuckDB close after acknowledged %s: %v\n", e.ID, closeErr)
		}
	}
	e.Kind = "outcome"
	e.Result = result
	e.EndedNS = time.Now().UnixNano()
	e.DurationNS = time.Since(started).Nanoseconds()
	e.EndedPhase = recoveryPhase(filepath.Dir(j.file.Name()))
	if err != nil {
		e.Error = err.Error()
	}
	if journalErr := j.emit(e); journalErr != nil {
		return e, journalErr
	}
	if closeErr != nil {
		return e, fmt.Errorf("engine close after %s: %w", e.ID, closeErr)
	}
	return e, nil
}

func recoveryNewConfig() (recoveryConfig, error) {
	c := recoveryConfig{Version: 1, RunID: os.Getenv("RECOVERY_RUN_ID"), CreatedNS: time.Now().UnixNano(), Catalog: "http://storage:8181", S3: "storage:8333", ReferenceClient: recoveryReferenceClient}
	c.Table = "recovery_" + strings.ReplaceAll(c.RunID, "-", "")
	var err error
	for _, setting := range []struct {
		key      string
		fallback int64
		target   *int64
	}{
		{"ROWS", 2000000, &c.Rows}, {"CUSTOMERS", 10000, &c.Customers}, {"BATCH_ROWS", 100000, &c.Batch},
		{"RECOVERY_TIMEOUT_S", 60, &c.TimeoutSeconds}, {"RECOVERY_DELAY_MS", 500, &c.DelayMillis}, {"RECOVERY_VISIBILITY_S", 30, &c.VisibilitySecs},
		{"RECOVERY_VERIFY_TIMEOUT_S", 300, &c.VerifyTimeoutSeconds},
	} {
		*setting.target, err = strconv.ParseInt(env(setting.key, strconv.FormatInt(setting.fallback, 10)), 10, 64)
		if err != nil {
			return c, fmt.Errorf("%s: %w", setting.key, err)
		}
	}
	writers, err := strconv.Atoi(env("WRITERS", "2"))
	if err != nil {
		return c, err
	}
	c.Writers = writers
	readers, err := strconv.Atoi(env("RECOVERY_READERS", "2"))
	if err != nil {
		return c, err
	}
	c.Readers = readers
	if value := os.Getenv("RECOVERY_BATCH_ROWS"); value != "" {
		c.Batch, err = strconv.ParseInt(value, 10, 64)
		if err != nil {
			return c, err
		}
	}
	return c, c.validate()
}

func recoverySeed() error {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, err := recoveryNewConfig()
	if err != nil {
		return err
	}
	if err = os.Mkdir(root, 0755); err != nil {
		return fmt.Errorf("new recovery directory required; preserve existing run and use recovery-verify: %w", err)
	}
	if err = recoverySyncDir(filepath.Dir(root)); err != nil {
		return err
	}
	if err = recoverySave(filepath.Join(root, "config.json"), c); err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	ctx, cancel := recoveryContext(root)
	defer cancel()
	bootstrap, err := recoveryOpenJournal(root, "bootstrap")
	if err != nil {
		return err
	}
	defer bootstrap.file.Close()
	setupctx, setupcancel := context.WithTimeout(ctx, time.Duration(c.TimeoutSeconds)*time.Second)
	db, err := recoveryDuck(setupctx, c)
	if err == nil {
		var version string
		var timeout, retries int64
		var globals bool
		err = db.QueryRowContext(setupctx, "SELECT version(),current_setting('http_timeout'),current_setting('http_retries'),current_setting('enable_global_s3_configuration')").Scan(&version, &timeout, &retries, &globals)
		if err == nil && (timeout != c.TimeoutSeconds || retries != 0 || globals) {
			err = errors.New("effective DuckDB HTTP/local credential settings differ from contract")
		}
		if err == nil {
			err = recoverySave(filepath.Join(root, "duckdb-settings.json"), map[string]any{"version": version, "http_timeout_seconds": timeout, "http_retries": retries, "enable_global_s3_configuration": globals, "utc_ns": time.Now().UnixNano()})
		}
		if err != nil {
			_ = db.Close()
		}
	}
	if err == nil {
		for _, q := range []string{"CREATE SCHEMA IF NOT EXISTS lake.lab", "CREATE TABLE lake.lab." + c.Table + " (event_id BIGINT,customer_id INTEGER,amount_cents BIGINT,writer_id INTEGER,batch_start BIGINT,batch_end BIGINT,payload_sha256 VARCHAR) WITH ('format-version'='2')"} {
			event := map[string]any{"kind": "intent", "run_id": c.RunID, "config_sha256": digest, "sql": q, "utc_ns": time.Now().UnixNano()}
			if err = bootstrap.emit(event); err != nil {
				break
			}
			_, queryErr := db.ExecContext(setupctx, q)
			result := "ok"
			errorText := ""
			if queryErr != nil {
				result = "ambiguous"
				errorText = queryErr.Error()
			}
			err = bootstrap.emit(map[string]any{"kind": "outcome", "run_id": c.RunID, "config_sha256": digest, "sql": q, "result": result, "error": errorText, "utc_ns": time.Now().UnixNano()})
			if err == nil {
				err = queryErr
			}
			if err != nil {
				break
			}
		}
		closeErr := db.Close()
		if err == nil {
			err = closeErr
		}
	}
	setupcancel()
	if err != nil {
		return err
	}
	if err = recoveryCatalogRecord(ctx, root, c, digest, "created-catalog"); err != nil {
		return err
	}
	if _, err = recoveryCatalogState(root, "created-catalog"); err != nil {
		return fmt.Errorf("assert newly created native Iceberg v2 metadata before seed: %w", err)
	}
	j, err := recoveryOpenJournal(root, "seed")
	if err != nil {
		return err
	}
	defer j.file.Close()
	checks, err := recoveryOpenJournal(root, "seed-checks")
	if err != nil {
		return err
	}
	defer checks.file.Close()
	for seq := int64(0); seq*c.Batch < c.Rows; seq++ {
		e, err := recoveryAttemptEvent(c, digest, -1, seq)
		if err != nil {
			return err
		}
		outcome, err := recoveryMutate(ctx, c, j, e)
		if err != nil {
			return err
		}
		if outcome.Result != "ok" {
			return fmt.Errorf("seed did not acknowledge %s (%s); preserve table/journals, never reseed: %s", e.ID, outcome.Result, outcome.Error)
		}
		checkctx, checkcancel := context.WithTimeout(ctx, time.Duration(c.TimeoutSeconds)*time.Second)
		count, hash, result, checkErr := recoveryReadBatch(checkctx, c, outcome, "duckdb")
		checkcancel()
		errorText := ""
		if checkErr != nil {
			errorText = checkErr.Error()
		}
		if err = checks.emit(map[string]any{"run_id": c.RunID, "operation_id": e.ID, "rows": count, "body_sha256": hash, "result": result, "error": errorText, "utc_ns": time.Now().UnixNano()}); err != nil {
			return err
		}
		if checkErr != nil || result != "ok" {
			return fmt.Errorf("seed exact verification failed (%s): %v", result, checkErr)
		}
	}
	if err = recoveryCatalogRecord(ctx, root, c, digest, "seed-catalog"); err != nil {
		return err
	}
	return recoverySave(filepath.Join(root, "seed-complete.json"), map[string]any{"run_id": c.RunID, "config_sha256": digest, "rows": c.Rows, "utc_ns": time.Now().UnixNano()})
}

func recoveryWriter() (returned error) {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	if _, err = os.Stat(filepath.Join(root, "seed-complete.json")); err != nil {
		return errors.New("verified complete seed required before live writers")
	}
	writer, err := strconv.Atoi(os.Getenv("RECOVERY_WRITER_ID"))
	if err != nil || writer < 0 || writer >= c.Writers {
		return errors.New("RECOVERY_WRITER_ID outside configured range")
	}
	name := fmt.Sprintf("writer-%03d", writer)
	j, err := recoveryOpenJournal(root, name)
	if err != nil {
		return err
	}
	defer j.file.Close()
	defer func() {
		if returned == nil {
			returned = recoverySave(filepath.Join(root, name+"-ended.json"), map[string]any{"run_id": c.RunID, "config_sha256": digest, "utc_ns": time.Now().UnixNano(), "phase": recoveryPhase(root), "clean_shutdown": true, "native_counters": recoveryNativeCounters()})
		}
	}()
	ctx, cancel := recoveryContext(root)
	defer cancel()
	for seq := int64(0); ctx.Err() == nil; seq++ {
		e, err := recoveryAttemptEvent(c, digest, writer, seq)
		if err != nil {
			return err
		}
		if _, err = recoveryMutate(ctx, c, j, e); err != nil {
			return err
		}
		if !recoveryWait(ctx, c.DelayMillis) {
			break
		}
	}
	return nil
}

type recoveryRowStream interface {
	Next() bool
	Scan(...any) error
	Err() error
	Close() error
}

func recoverySelect(c recoveryConfig, e recoveryEvent, engine string) string {
	table := "lake.lab." + c.Table
	if engine == "clickhouse" {
		table = "lake.`lab." + c.Table + "`"
	}
	return fmt.Sprintf("SELECT %s FROM %s WHERE event_id>=%d AND event_id<%d ORDER BY event_id LIMIT %d", strings.Join(recoveryColumns, ","), table, e.Lo, e.Hi, e.Hi-e.Lo+1)
}

// Each query takes a fresh catalog view. No SELECT is retried within an
// observation; failures remain visible in the reader's independent journal.
func recoveryQuery(ctx context.Context, c recoveryConfig, e recoveryEvent, engine string) (recoveryRowStream, func() error, error) {
	return recoveryQuerySQL(ctx, c, recoverySelect(c, e, engine), engine, e.Hi-e.Lo+1)
}

func recoveryQuerySQL(ctx context.Context, c recoveryConfig, query, engine string, resultLimit int64) (recoveryRowStream, func() error, error) {
	if engine == "duckdb" {
		db, err := recoveryDuck(ctx, c)
		if err != nil {
			return nil, nil, err
		}
		rows, err := db.QueryContext(ctx, query)
		if err != nil {
			_ = db.Close()
			return nil, nil, err
		}
		return rows, db.Close, nil
	}
	conn, err := ch()
	if err != nil {
		return nil, nil, err
	}
	if err = catalog(ctx, conn); err != nil {
		_ = conn.Close()
		return nil, nil, err
	}
	seconds := c.TimeoutSeconds
	if deadline, ok := ctx.Deadline(); ok {
		seconds = max(1, int64((time.Until(deadline)+time.Second-1)/time.Second))
	}
	bounded := clickhouse.Context(ctx, clickhouse.WithSettings(clickhouse.Settings{"max_result_rows": resultLimit, "max_rows_to_read": 100000000, "max_bytes_to_read": int64(16) << 30, "max_execution_time": seconds, "timeout_before_checking_execution_speed": 0, "result_overflow_mode": "throw"}))
	rows, err := conn.Query(bounded, query)
	if err != nil {
		_ = conn.Close()
		return nil, nil, err
	}
	return rows, conn.Close, nil
}

func recoveryReadBatch(ctx context.Context, c recoveryConfig, target recoveryEvent, engine string) (int64, string, string, error) {
	rows, closeEngine, err := recoveryQuery(ctx, c, target, engine)
	if err != nil {
		return 0, "", "fail", err
	}
	defer closeEngine()
	defer rows.Close()
	count := int64(0)
	hash := newRecoveryBodyHash()
	for rows.Next() {
		row, err := recoveryScan(rows)
		if err != nil {
			return count, "", "fail", err
		}
		if row.ID != target.Lo+count || row != recoveryExpected(c, target, row.ID) {
			return count, "", "mismatch", fmt.Errorf("wrong/duplicate read body: observed=%+v expected=%+v next_id=%d", row, recoveryExpected(c, target, row.ID), target.Lo+count)
		}
		if err = hash.write(row); err != nil {
			return count, "", "fail", err
		}
		count++
	}
	if err = rows.Err(); err != nil {
		return count, "", "fail", err
	}
	if count == 0 {
		return 0, hash.finish(), "stale", nil
	}
	if count != target.Hi-target.Lo {
		return count, hash.finish(), "mismatch", errors.New("partially visible acknowledged atomic batch")
	}
	return count, hash.finish(), "ok", nil
}

func recoveryReader() (returned error) {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	reader, err := strconv.Atoi(os.Getenv("RECOVERY_READER_ID"))
	if err != nil || reader < 0 || reader >= c.Readers {
		return errors.New("RECOVERY_READER_ID outside configured range")
	}
	engine := env("RECOVERY_READ_ENGINE", "clickhouse")
	if engine != "duckdb" && engine != "clickhouse" {
		return errors.New("RECOVERY_READ_ENGINE must be duckdb or clickhouse")
	}
	name := fmt.Sprintf("reader-%03d", reader)
	j, err := recoveryOpenJournal(root, name)
	if err != nil {
		return err
	}
	defer j.file.Close()
	defer func() {
		if returned == nil {
			returned = recoverySave(filepath.Join(root, name+"-ended.json"), map[string]any{"run_id": c.RunID, "config_sha256": digest, "engine": engine, "utc_ns": time.Now().UnixNano(), "phase": recoveryPhase(root), "clean_shutdown": true, "native_counters": recoveryNativeCounters()})
		}
	}()
	ctx, cancel := recoveryContext(root)
	defer cancel()
	for seq := int64(0); ctx.Err() == nil; seq++ {
		attempts, err := recoveryReadAttempts(root, c, digest, true)
		if err != nil {
			return err
		}
		var acks []recoveryEvent
		for _, attempt := range attempts {
			if attempt.Outcome != nil && attempt.Outcome.Result == "ok" {
				acks = append(acks, *attempt.Outcome)
			}
		}
		if len(acks) == 0 {
			if !recoveryWait(ctx, c.DelayMillis) {
				break
			}
			continue
		}
		sort.Slice(acks, func(i, j int) bool { return acks[i].EndedNS < acks[j].EndedNS })
		target := acks[0]
		locality := "historical"
		if seq%2 == 0 {
			target = acks[len(acks)-1]
			locality = "recent"
		}
		e := target
		e.Kind = "read"
		e.Actor = name
		e.Engine = engine
		e.Locality = locality
		e.AckNS = target.EndedNS
		e.StartedNS = time.Now().UnixNano()
		e.ReadSequence = seq
		e.Phase = recoveryPhase(root)
		e.Error = ""
		opctx, opcancel := context.WithTimeout(ctx, time.Duration(c.TimeoutSeconds)*time.Second)
		started := time.Now()
		e.Rows, e.BodyHash, e.Result, err = recoveryReadBatch(opctx, c, target, engine)
		opcancel()
		e.EndedNS = time.Now().UnixNano()
		e.DurationNS = time.Since(started).Nanoseconds()
		e.EndedPhase = recoveryPhase(root)
		if err != nil {
			e.Error = err.Error()
		}
		if e.Result == "stale" && e.EndedNS-e.AckNS > int64(time.Duration(c.VisibilitySecs)*time.Second) {
			e.Result = "visibility_timeout"
		}
		if err = j.emit(e); err != nil {
			return err
		}
		if e.Result == "mismatch" {
			return errors.New("independent reader observed contract violation; retain all evidence")
		}
		if !recoveryWait(ctx, c.DelayMillis) {
			break
		}
	}
	return nil
}

func recoveryCatalogRecord(ctx context.Context, root string, c recoveryConfig, digest, name string) error {
	return localCatalogRecord(ctx, root, c.Table, c.RunID, digest, name)
}

func localCatalogRecord(ctx context.Context, root, table, runID, digest, name string) error {
	client := &http.Client{Timeout: 10 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	request := func(method, path string, body io.Reader) ([]byte, error) {
		req, err := http.NewRequestWithContext(ctx, method, "http://storage:8181"+path, body)
		if err != nil {
			return nil, err
		}
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		resp, err := client.Do(req)
		if err != nil {
			return nil, err
		}
		defer resp.Body.Close()
		data, err := io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
		if err != nil {
			return nil, err
		}
		if len(data) > 8<<20 {
			return nil, errors.New("oversized catalog metadata response")
		}
		if resp.StatusCode/100 != 2 {
			return nil, fmt.Errorf("local catalog status %d", resp.StatusCode)
		}
		return data, nil
	}
	// Match the engines' explicit local OAuth credentials. Never discover host
	// profiles, credentials, or endpoints, and never follow an off-lab redirect.
	form := url.Values{"grant_type": {"client_credentials"}, "client_id": {"demo"}, "client_secret": {"demo"}}
	tokenBody, err := request("POST", "/v1/oauth/tokens", strings.NewReader(form.Encode()))
	var token struct {
		Token string `json:"access_token"`
	}
	if err == nil {
		err = json.Unmarshal(tokenBody, &token)
	}
	var body []byte
	if err == nil && token.Token != "" {
		req, e := http.NewRequestWithContext(ctx, "GET", "http://storage:8181/v1/namespaces/lab/tables/"+url.PathEscape(table), nil)
		err = e
		if err == nil {
			req.Header.Set("Authorization", "Bearer "+token.Token)
			resp, e := client.Do(req)
			err = e
			if err == nil {
				body, err = io.ReadAll(io.LimitReader(resp.Body, (8<<20)+1))
				_ = resp.Body.Close()
				if resp.StatusCode/100 != 2 {
					err = fmt.Errorf("local catalog status %d", resp.StatusCode)
				}
				if len(body) > 8<<20 {
					err = errors.New("oversized metadata")
				}
			}
		}
	} else if err == nil {
		err = errors.New("empty local catalog token")
	}
	value := map[string]any{"run_id": runID, "config_sha256": digest, "table": table, "utc_ns": time.Now().UnixNano(), "available": err == nil}
	if err == nil {
		if !json.Valid(body) {
			err = errors.New("invalid catalog metadata JSON")
		} else {
			// Snapshot IDs are int64. Decoding through any would round them via
			// float64 before the persisted snapshot identity is read back.
			value["load_table"] = json.RawMessage(body)
			value["body_sha256"] = recoveryDigest(body)
		}
	}
	if err != nil {
		value["available"] = false
		value["error"] = err.Error()
	}
	return recoverySave(filepath.Join(root, name+".json"), value)
}

func recoveryVerify() (returned error) {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	verificationID := os.Getenv("RECOVERY_VERIFICATION_ID")
	output, err := recoveryVerificationDir(root, verificationID)
	if err != nil {
		return err
	}
	diagnosticText := env("RECOVERY_DIAGNOSTIC", "false")
	if diagnosticText != "true" && diagnosticText != "false" {
		return errors.New("RECOVERY_DIAGNOSTIC must be true or false")
	}
	diagnostic := diagnosticText == "true"
	defer func() {
		if returned != nil {
			_ = recoverySave(filepath.Join(output, "failure.json"), map[string]any{"run_id": c.RunID, "verification_id": verificationID, "config_sha256": digest, "error": returned.Error(), "utc_ns": time.Now().UnixNano()})
		}
	}()
	if err = recoverySave(filepath.Join(output, "invocation.json"), map[string]any{"run_id": c.RunID, "verification_id": verificationID, "config_sha256": digest, "diagnostic": diagnostic, "utc_ns": time.Now().UnixNano()}); err != nil {
		return err
	}
	witnesses, err := recoveryWitnessHashes(root)
	if err != nil {
		return err
	}
	attempts, err := recoveryReadAttempts(root, c, digest, false)
	issues := recoveryActorIssues(root, c, digest)
	actorsClean := len(issues) == 0
	historyValid := err == nil
	if err != nil {
		if !diagnostic {
			return err
		}
		issues = append(issues, "strict mutation journal: "+err.Error())
		// Diagnostic prefix replay never makes an incomplete final journal valid.
		// It can classify a complete prior intent as ambiguous after a torn outcome;
		// the strict failure and original bytes remain in the evidence/verdict.
		attempts, err = recoveryReadAttempts(root, c, digest, true)
		if err != nil {
			return err
		}
	}
	err = recoveryVerifyReaders(root, c, digest, attempts)
	readersValid := err == nil
	if err != nil {
		issues = append(issues, "reader qualification: "+err.Error())
	}
	if err = recoveryRequireQualification(issues, diagnostic); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), time.Duration(c.VerifyTimeoutSeconds)*time.Second)
	defer cancel()
	if err = recoveryCatalogRecord(ctx, output, c, digest, "verify-start-catalog"); err != nil {
		return err
	}
	initial, err := recoveryCatalogState(root, "created-catalog")
	if err != nil {
		return err
	}
	before, err := recoveryCatalogState(output, "verify-start-catalog")
	if err != nil {
		return err
	}
	if initial.UUID != before.UUID {
		return errors.New("native catalog table UUID changed across recovery")
	}
	exports := map[string]recoveryExportProof{}
	for _, engine := range []string{"duckdb", "clickhouse"} {
		proof, err := recoveryExport(ctx, output, c, digest, verificationID, attempts, engine)
		if err != nil {
			return fmt.Errorf("%s full export: %w", engine, err)
		}
		exports[engine] = proof
		name := "verify-" + engine + "-catalog"
		if err = recoveryCatalogRecord(ctx, output, c, digest, name); err != nil {
			return err
		}
		after, err := recoveryCatalogState(output, name)
		if err != nil {
			return err
		}
		if after != before {
			return errors.New("catalog snapshot changed across quiescent full-engine verification")
		}
	}
	if !reflect.DeepEqual(exports["duckdb"].Proof, exports["clickhouse"].Proof) {
		return errors.New("full-engine body proofs differ")
	}
	storageStarted := time.Now()
	db, err := recoveryDuck(ctx, c)
	if err != nil {
		return err
	}
	versions, versionErr := qualifiedNativeVersions(ctx, db)
	if versionErr == nil {
		versionErr = recoverySave(filepath.Join(output, "native-versions.json"), versions)
	}
	var versionsSHA string
	if versionErr == nil {
		versionsSHA, versionErr = recoveryFileHash(filepath.Join(output, "native-versions.json"))
	}
	if versionErr != nil {
		return errors.Join(versionErr, db.Close())
	}
	versionsRef := map[string]any{"file": "native-versions.json", "sha256": versionsSHA}
	storage, measureErr := recoveryMeasureStorage(ctx, db, before.Metadata, before.Snapshot, exports["duckdb"].Proof.Rows)
	closeErr := db.Close()
	measureErr = errors.Join(measureErr, closeErr)
	storageReport := map[string]any{
		"run_id": c.RunID, "verification_id": verificationID, "config_sha256": digest, "snapshot": before,
		"native_versions": versionsRef, "passed": measureErr == nil, "raw_manifest_entries": storage.Entries, "excluded_deleted_entries": storage.ExcludedDeleted, "files": storage.Files,
		"started_ns": storageStarted.UnixNano(), "ended_ns": time.Now().UnixNano(), "duration_ns": time.Since(storageStarted).Nanoseconds(), "native_counters": recoveryNativeCounters(),
		"scope": "Committed live Parquet files at the verified snapshot. Compression ratio sums encoded column-chunk uncompressed/compressed page bytes, including page headers once per leaf column/row group; not CSV/raw logical/RAM/allocated-disk compression. File/footer bytes are separate. Native counters are cumulative within this verifier container and include earlier full-body exports; duration covers only storage measurement/setup/save preparation.",
	}
	if measureErr == nil {
		storageReport["totals"] = storage.Totals
		storageReport["encoded_parquet_chunk_compression_ratio"] = storage.Ratio
	} else {
		storageReport["unavailable_error"] = measureErr.Error()
	}
	if err = recoverySave(filepath.Join(output, "storage-metrics.json"), storageReport); err != nil {
		return errors.Join(measureErr, err)
	}
	if measureErr != nil {
		return measureErr
	}
	storageSHA, err := recoveryFileHash(filepath.Join(output, "storage-metrics.json"))
	if err != nil {
		return err
	}
	if err = recoveryCatalogRecord(ctx, output, c, digest, "verify-storage-catalog"); err != nil {
		return err
	}
	afterStorage, err := recoveryCatalogState(output, "verify-storage-catalog")
	if err != nil {
		return err
	}
	if afterStorage != before {
		return errors.New("catalog snapshot changed during native storage measurement")
	}
	current, err := recoveryWitnessHashes(root)
	if err != nil {
		return err
	}
	if !reflect.DeepEqual(current, witnesses) {
		return errors.New("histories changed during final verification")
	}
	resultPath := filepath.Join(output, "verified.json")
	qualified := !diagnostic && len(issues) == 0
	if err = recoverySave(resultPath, map[string]any{"passed": qualified, "qualified": qualified, "integrity_passed": true, "actor_lifecycle_passed": actorsClean, "strict_mutation_history_passed": historyValid, "reader_contract_passed": readersValid, "diagnostic": diagnostic, "qualification_issues": issues, "run_id": c.RunID, "verification_id": verificationID, "config_sha256": digest, "history_sha256": witnesses, "exports": exports, "native_versions": versionsRef, "storage_metrics": map[string]any{"file": "storage-metrics.json", "sha256": storageSHA, "snapshot": before}, "snapshot": before, "utc_ns": time.Now().UnixNano(), "scope": "Both engines independently stream every body from one quiescent catalog snapshot; Go reconciles each against durable intents/outcomes. Diagnostic prefix replay never qualifies a malformed/incomplete journal or failed actor. Whole-batch unacknowledged application remains ambiguous. Same-host/process-fault evidence, not physical power-loss durability or a performance ranking."}); err != nil {
		return err
	}
	fmt.Println(resultPath)
	if !qualified {
		return fmt.Errorf("diagnostic integrity export complete; overall qualification remains FAILED: %s", resultPath)
	}
	return nil
}

type recoveryExportProof struct {
	RunID          string         `json:"run_id"`
	VerificationID string         `json:"verification_id"`
	ConfigHash     string         `json:"config_sha256"`
	File           string         `json:"csv_file"`
	FileSHA        string         `json:"csv_sha256"`
	Proof          recoveryProof  `json:"proof"`
	StartedNS      int64          `json:"started_ns"`
	EndedNS        int64          `json:"ended_ns"`
	DurationNS     int64          `json:"duration_ns"`
	NativeCounters map[string]any `json:"native_counters"`
}

func recoveryExport(ctx context.Context, root string, c recoveryConfig, digest, verificationID string, attempts []recoveryAttempt, engine string) (recoveryExportProof, error) {
	started := time.Now()
	report := recoveryExportProof{RunID: c.RunID, VerificationID: verificationID, ConfigHash: digest, File: engine + "-rows.csv", StartedNS: started.UnixNano()}
	limit := int64(1)
	for _, attempt := range attempts {
		limit += attempt.Intent.Hi - attempt.Intent.Lo
	}
	table := "lake.lab." + c.Table
	if engine == "clickhouse" {
		table = "lake.`lab." + c.Table + "`"
	}
	rows, closeEngine, err := recoveryQuerySQL(ctx, c, fmt.Sprintf("SELECT %s FROM %s ORDER BY event_id LIMIT %d", strings.Join(recoveryColumns, ","), table, limit), engine, limit)
	if err != nil {
		return report, err
	}
	defer closeEngine()
	defer rows.Close()
	file, err := os.OpenFile(filepath.Join(root, report.File), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return report, err
	}
	writer := csv.NewWriter(file)
	if err = writer.Write(recoveryColumns); err == nil {
		for rows.Next() {
			row, scanErr := recoveryScan(rows)
			if scanErr != nil {
				err = scanErr
				break
			}
			if err = writer.Write(row.fields()); err != nil {
				break
			}
		}
	}
	if err == nil {
		err = rows.Err()
	}
	writer.Flush()
	if err == nil {
		err = writer.Error()
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err == nil {
		err = closeErr
	}
	if err != nil {
		return report, err
	}
	if err = recoverySyncDir(root); err != nil {
		return report, err
	}
	input, err := os.Open(filepath.Join(root, report.File))
	if err != nil {
		return report, err
	}
	report.Proof, err = recoveryReconcile(input, c, attempts)
	_ = input.Close()
	if err != nil {
		return report, err
	}
	report.FileSHA, err = recoveryFileHash(filepath.Join(root, report.File))
	if err != nil {
		return report, err
	}
	report.EndedNS = time.Now().UnixNano()
	report.DurationNS = time.Since(started).Nanoseconds()
	report.NativeCounters = recoveryNativeCounters()
	if err = recoverySave(filepath.Join(root, engine+"-proof.json"), report); err != nil {
		return report, err
	}
	return report, nil
}

type recoverySnapshot struct {
	UUID     string `json:"table_uuid"`
	Snapshot int64  `json:"snapshot_id"`
	Metadata string `json:"metadata_location"`
}

func recoveryCatalogState(root, name string) (recoverySnapshot, error) {
	var state recoverySnapshot
	data, err := os.ReadFile(filepath.Join(root, name+".json"))
	if err != nil {
		return state, err
	}
	var record struct {
		Available bool `json:"available"`
		Table     struct {
			Location string `json:"metadata-location"`
			Metadata struct {
				UUID     string `json:"table-uuid"`
				Version  int    `json:"format-version"`
				Snapshot int64  `json:"current-snapshot-id"`
			} `json:"metadata"`
		} `json:"load_table"`
	}
	if err = json.Unmarshal(data, &record); err != nil {
		return state, err
	}
	if !record.Available {
		return state, errors.New("native catalog metadata unavailable")
	}
	if record.Table.Metadata.Version != 2 || record.Table.Metadata.UUID == "" || record.Table.Location == "" {
		return state, errors.New("native catalog must report Iceberg v2, table UUID and metadata location")
	}
	return recoverySnapshot{UUID: record.Table.Metadata.UUID, Snapshot: record.Table.Metadata.Snapshot, Metadata: record.Table.Location}, nil
}
