package main

import (
	"bytes"
	"database/sql"
	"encoding/csv"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func recoveryTestConfig() recoveryConfig {
	return recoveryConfig{Version: 1, RunID: "5742e029-c49e-4e57-8832-ddc6495e1e8d", Table: "recovery_5742e029c49e4e578832ddc6495e1e8d", Rows: 4, Customers: 3, Batch: 2, Writers: 2, Readers: 1, TimeoutSeconds: 1, VerifyTimeoutSeconds: 300, VisibilitySecs: 30, CreatedNS: 1, Catalog: "http://storage:8181", S3: "storage:8333", ReferenceClient: recoveryReferenceClient}
}

func recoveryTestAttempt(t *testing.T, c recoveryConfig, writer int, seq int64, result string) recoveryAttempt {
	t.Helper()
	e, err := recoveryAttemptEvent(c, "digest", writer, seq)
	if err != nil {
		t.Fatal(err)
	}
	a := recoveryAttempt{Intent: e}
	if result != "pending" {
		out := e
		out.Kind = "outcome"
		out.Result = result
		out.EndedNS = out.StartedNS + 1
		out.DurationNS = 1
		if result != "ok" {
			out.Error = "synthetic pre-submission/ambiguous RPC error"
		}
		a.Outcome = &out
	}
	return a
}

func recoveryTestCSV(t *testing.T, c recoveryConfig, attempts []recoveryAttempt, present map[int64]bool) []byte {
	t.Helper()
	var data bytes.Buffer
	w := csv.NewWriter(&data)
	if err := w.Write(recoveryColumns); err != nil {
		t.Fatal(err)
	}
	for _, a := range attempts {
		for id := a.Intent.Lo; id < a.Intent.Hi; id++ {
			if present == nil || present[id] {
				if err := w.Write(recoveryExpected(c, a.Intent, id).fields()); err != nil {
					t.Fatal(err)
				}
			}
		}
	}
	w.Flush()
	if err := w.Error(); err != nil {
		t.Fatal(err)
	}
	return data.Bytes()
}

func TestRecoveryOraclePreservesAmbiguity(t *testing.T) {
	c := recoveryTestConfig()
	attempts := []recoveryAttempt{recoveryTestAttempt(t, c, -1, 0, "ok"), recoveryTestAttempt(t, c, -1, 1, "ok"), recoveryTestAttempt(t, c, 0, 0, "ambiguous"), recoveryTestAttempt(t, c, 1, 0, "fail"), recoveryTestAttempt(t, c, 0, 1, "pending")}
	present := map[int64]bool{0: true, 1: true, 2: true, 3: true, 4: true, 5: true}
	proof, err := recoveryReconcile(bytes.NewReader(recoveryTestCSV(t, c, attempts, present)), c, attempts)
	if err != nil {
		t.Fatal(err)
	}
	if proof.AckRows != 4 || proof.AckBatches != 2 || proof.AmbiguousApplied != 1 || proof.AmbiguousAbsent != 1 || proof.KnownFailed != 1 || proof.Rows != 6 || len(proof.BodySHA256) != 64 {
		t.Fatalf("wrong ambiguity classification: %+v", proof)
	}
}

func TestRecoveryOracleRejectsMissingPartialDuplicateWrongAndUnexpectedBodies(t *testing.T) {
	c := recoveryTestConfig()
	a := recoveryTestAttempt(t, c, -1, 0, "ok")
	attempts := []recoveryAttempt{a}
	valid := recoveryTestCSV(t, c, attempts, nil)
	records, err := csv.NewReader(bytes.NewReader(valid)).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	tests := map[string][][]string{
		"missing ACK":   {records[0]},
		"partial batch": {records[0], records[1]},
		"duplicate ID":  {records[0], records[1], records[1], records[2]},
		"unexpected ID": {records[0], records[1], records[2], recoveryExpected(c, a.Intent, 17).fields()},
	}
	for col := 0; col < 7; col++ {
		wrong := append([]string(nil), records[1]...)
		wrong[col] = "999"
		tests["wrong column "+recoveryColumns[col]] = [][]string{records[0], wrong, records[2]}
	}
	for name, rows := range tests {
		t.Run(name, func(t *testing.T) {
			var data bytes.Buffer
			w := csv.NewWriter(&data)
			if err := w.WriteAll(rows); err != nil {
				t.Fatal(err)
			}
			if _, err := recoveryReconcile(&data, c, attempts); err == nil {
				t.Fatal("accepted corrupt/missing/unexpected body")
			}
		})
	}
	for _, result := range []string{"fail", "ambiguous", "pending"} {
		t.Run(result+" partial", func(t *testing.T) {
			a := recoveryTestAttempt(t, c, -1, 0, result)
			data := recoveryTestCSV(t, c, []recoveryAttempt{a}, map[int64]bool{0: true})
			if _, err := recoveryReconcile(bytes.NewReader(data), c, []recoveryAttempt{a}); err == nil {
				t.Fatal("accepted partial unacknowledged application")
			}
		})
	}
	a = recoveryTestAttempt(t, c, -1, 0, "fail")
	if _, err := recoveryReconcile(bytes.NewReader(valid), c, []recoveryAttempt{a}); err == nil {
		t.Fatal("accepted applied pre-submission failed request")
	}
}

func TestRecoveryJournalFinalEOFIsStrict(t *testing.T) {
	path := filepath.Join(t.TempDir(), "journal.jsonl")
	e := recoveryTestAttempt(t, recoveryTestConfig(), 0, 0, "ok").Intent
	data, err := json.Marshal(e)
	if err != nil {
		t.Fatal(err)
	}
	for _, tail := range [][]byte{data, []byte(`{"kind":"inte`)} {
		if err = os.WriteFile(path, append(append(append([]byte(nil), data...), '\n'), tail...), 0644); err != nil {
			t.Fatal(err)
		}
		if events, err := recoveryEvents(path, true); err != nil || len(events) != 1 {
			t.Fatalf("live final partial handling: %v %d", err, len(events))
		}
		if _, err := recoveryEvents(path, false); err == nil {
			t.Fatal("final unterminated line accepted")
		}
	}
	if err = os.WriteFile(path, []byte("{}\nBROKEN\n"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := recoveryEvents(path, true); err == nil {
		t.Fatal("ignored non-final malformed journal line")
	}
}

func recoveryWriteAttempts(t *testing.T, root string, c recoveryConfig, attempts []recoveryAttempt) {
	t.Helper()
	for writer := -1; writer < c.Writers; writer++ {
		name := "seed"
		if writer >= 0 {
			name = fmt.Sprintf("writer-%03d", writer)
		}
		j, err := recoveryOpenJournal(root, name)
		if err != nil {
			t.Fatal(err)
		}
		for _, a := range attempts {
			if a.Intent.Writer == writer {
				if err = j.emit(a.Intent); err != nil {
					t.Fatal(err)
				}
				if a.Outcome != nil {
					if err = j.emit(*a.Outcome); err != nil {
						t.Fatal(err)
					}
				}
			}
		}
		if err = j.file.Close(); err != nil {
			t.Fatal(err)
		}
	}
}

func TestRecoveryHistoryChecksIdentitySequenceAndSeedCompleteness(t *testing.T) {
	c := recoveryTestConfig()
	valid := []recoveryAttempt{recoveryTestAttempt(t, c, -1, 0, "ok"), recoveryTestAttempt(t, c, -1, 1, "ok"), recoveryTestAttempt(t, c, 0, 0, "ok"), recoveryTestAttempt(t, c, 1, 0, "ambiguous")}
	t.Run("valid", func(t *testing.T) {
		root := t.TempDir()
		recoveryWriteAttempts(t, root, c, valid)
		got, err := recoveryReadAttempts(root, c, "digest", false)
		if err != nil || len(got) != 4 {
			t.Fatalf("valid history rejected: %v %+v", err, got)
		}
	})
	for _, mode := range []string{"cross-run", "config-hash", "bounds", "duplicate intent", "wrong outcome", "seed omitted"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			attempts := append([]recoveryAttempt(nil), valid...)
			switch mode {
			case "cross-run":
				attempts[2].Intent.RunID = "other"
			case "config-hash":
				attempts[2].Intent.ConfigHash = "other"
			case "bounds":
				attempts[2].Intent.Hi++
			case "duplicate intent":
				attempts = append(attempts, attempts[2])
			case "wrong outcome":
				out := *attempts[2].Outcome
				out.StartedNS++
				attempts[2].Outcome = &out
			case "seed omitted":
				attempts = attempts[1:]
			}
			recoveryWriteAttempts(t, root, c, attempts)
			if _, err := recoveryReadAttempts(root, c, "digest", false); err == nil {
				t.Fatal("accepted invalid history")
			}
		})
	}
}

func TestRecoveryReaderSuccessHashMustMatchIndependentOracle(t *testing.T) {
	c := recoveryTestConfig()
	ack := recoveryTestAttempt(t, c, -1, 0, "ok")
	for _, mode := range []string{"valid", "wrong hash", "unwitnessed", "stale pre-ACK", "partial successful read", "visibility bound"} {
		t.Run(mode, func(t *testing.T) {
			root := t.TempDir()
			j, err := recoveryOpenJournal(root, "reader-000")
			if err != nil {
				t.Fatal(err)
			}
			for seq, locality := range []string{"recent", "historical"} {
				e := *ack.Outcome
				e.Kind = "read"
				e.Actor = "reader-000"
				e.Engine = "clickhouse"
				e.Locality = locality
				e.ReadSequence = int64(seq)
				e.AckNS = ack.Outcome.EndedNS
				e.StartedNS = e.AckNS + 1
				e.EndedNS = e.StartedNS + 1
				e.Rows = e.Hi - e.Lo
				h := newRecoveryBodyHash()
				for id := e.Lo; id < e.Hi; id++ {
					if err = h.write(recoveryExpected(c, e, id)); err != nil {
						t.Fatal(err)
					}
				}
				e.BodyHash = h.finish()
				if seq == 1 {
					switch mode {
					case "wrong hash":
						e.BodyHash = strings.Repeat("0", 64)
					case "unwitnessed":
						e.ID = "unknown"
					case "stale pre-ACK":
						e.StartedNS = e.AckNS - 1
					case "partial successful read":
						e.Rows--
					case "visibility bound":
						e.Result = "visibility_timeout"
						e.Rows = 0
					}
				}
				if err = j.emit(e); err != nil {
					t.Fatal(err)
				}
			}
			if err = j.file.Close(); err != nil {
				t.Fatal(err)
			}
			err = recoveryVerifyReaders(root, c, "digest", []recoveryAttempt{ack})
			if (err == nil) != (mode == "valid") {
				t.Fatalf("reader verification mode %s: %v", mode, err)
			}
		})
	}
}

func TestRecoveryConfigAndWriterRanges(t *testing.T) {
	c := recoveryTestConfig()
	if err := c.validate(); err != nil {
		t.Fatal(err)
	}
	seen := map[int64]bool{}
	for writer := 0; writer < c.Writers; writer++ {
		for seq := int64(0); seq < 7; seq++ {
			lo, hi, err := recoveryBounds(c, writer, seq)
			if err != nil {
				t.Fatal(err)
			}
			for id := lo; id < hi; id++ {
				if id < c.Rows || seen[id] {
					t.Fatal("writer ranges overlap")
				}
				seen[id] = true
			}
		}
	}
	for _, mutate := range []func(*recoveryConfig){func(c *recoveryConfig) { c.Table = "events;DROP TABLE events" }, func(c *recoveryConfig) { c.RunID = "../../escape" }, func(c *recoveryConfig) { c.Catalog = "https://not-the-lab.invalid" }, func(c *recoveryConfig) { c.Batch = 0 }, func(c *recoveryConfig) { c.TimeoutSeconds = 0 }} {
		bad := c
		mutate(&bad)
		if err := bad.validate(); err == nil {
			t.Fatal("accepted unsafe config")
		}
	}
	root := filepath.Join(t.TempDir(), "recovery-"+c.RunID)
	if err := os.Mkdir(root, 0755); err != nil {
		t.Fatal(err)
	}
	if err := recoverySave(filepath.Join(root, "config.json"), c); err != nil {
		t.Fatal(err)
	}
	if _, _, err := recoveryLoad(root); err != nil {
		t.Fatal(err)
	}
	if err := recoverySave(filepath.Join(root, "config.json"), c); err == nil {
		t.Fatal("existing configuration overwritten")
	}
	if _, _, err := recoveryLoad(filepath.Dir(root)); err == nil {
		t.Fatal("accepted mismatched/missing config directory")
	}
}

type recoveryScanFixture []any

func (values recoveryScanFixture) Scan(targets ...any) error {
	for i, target := range targets {
		if err := target.(sql.Scanner).Scan(values[i]); err != nil {
			return err
		}
	}
	return nil
}
func TestRecoveryScansNativeWidthsAndRejectsNullBodies(t *testing.T) {
	values := recoveryScanFixture{int64(0), int32(0), int64(1), int32(-1), int64(0), int64(2), recoveryDigest([]byte("0"))}
	row, err := recoveryScan(values)
	if err != nil {
		t.Fatal(err)
	}
	c := recoveryTestConfig()
	a := recoveryTestAttempt(t, c, -1, 0, "ok")
	if row != recoveryExpected(c, a.Intent, 0) {
		t.Fatal("native numeric widths changed body")
	}
	for i := range values {
		bad := append(recoveryScanFixture(nil), values...)
		bad[i] = nil
		if _, err := recoveryScan(bad); err == nil {
			t.Fatalf("accepted NULL column %d", i)
		}
	}
}

func TestRecoveryVerificationDestinationIsUniqueAndOwned(t *testing.T) {
	root := t.TempDir()
	first := "5742e029-c49e-4e57-8832-ddc6495e1e8d"
	second := "6742e029-c49e-4e57-8832-ddc6495e1e8d"
	path, err := recoveryVerificationDir(root, first)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(path, "original.csv"), []byte("preserved"), 0644); err != nil {
		t.Fatal(err)
	}
	if _, err = recoveryVerificationDir(root, first); err == nil {
		t.Fatal("reused existing verification UUID")
	}
	other, err := recoveryVerificationDir(root, second)
	if err != nil || other == path {
		t.Fatalf("second unique verification failed: %v", err)
	}
	if data, err := os.ReadFile(filepath.Join(path, "original.csv")); err != nil || string(data) != "preserved" {
		t.Fatal("older export changed")
	}
	for _, id := range []string{"", "../../escape", "BAD", "5742e029-c49e-1e57-8832-ddc6495e1e8d"} {
		if _, err := recoveryVerificationDir(root, id); err == nil {
			t.Fatal("accepted unsafe verification identifier")
		}
	}
	t.Run("symlink parent", func(t *testing.T) {
		root := t.TempDir()
		foreign := t.TempDir()
		if err := os.Symlink(foreign, filepath.Join(root, "verifications")); err != nil {
			t.Fatal(err)
		}
		if _, err := recoveryVerificationDir(root, first); err == nil {
			t.Fatal("followed symlinked verification parent")
		}
		entries, err := os.ReadDir(foreign)
		if err != nil || len(entries) != 0 {
			t.Fatal("modified symlink target")
		}
	})
}

func TestRecoveryMissingActorCompletionBlocksQualificationButNotDiagnosticOracle(t *testing.T) {
	c := recoveryTestConfig()
	root := t.TempDir()
	issues := recoveryActorIssues(root, c, "digest")
	if len(issues) != c.Writers+c.Readers {
		t.Fatalf("missing actors not recorded: %+v", issues)
	}
	if err := recoveryRequireQualification(issues, false); err == nil {
		t.Fatal("qualified missing actor completions")
	}
	if err := recoveryRequireQualification(issues, true); err != nil {
		t.Fatal("diagnostic must still permit body reconciliation")
	}
	attempts := []recoveryAttempt{recoveryTestAttempt(t, c, -1, 0, "ok"), recoveryTestAttempt(t, c, -1, 1, "ok"), recoveryTestAttempt(t, c, 0, 0, "pending")}
	proof, err := recoveryReconcile(bytes.NewReader(recoveryTestCSV(t, c, attempts, nil)), c, attempts)
	if err != nil {
		t.Fatal(err)
	}
	if proof.AckRows != c.Rows || proof.AmbiguousApplied != 1 || proof.AmbiguousBatches[0].ID != attempts[2].Intent.ID {
		t.Fatal("pending durable intent was lost or promoted to ACK")
	}
	for _, actor := range []string{"writer-000", "writer-001", "reader-000"} {
		if err := recoverySave(filepath.Join(root, actor+"-ended.json"), map[string]any{"run_id": c.RunID, "config_sha256": "digest", "clean_shutdown": true}); err != nil {
			t.Fatal(err)
		}
	}
	if issues := recoveryActorIssues(root, c, "digest"); len(issues) != 0 {
		t.Fatalf("clean actor evidence rejected: %+v", issues)
	}
	if issues := recoveryActorIssues(root, c, "different"); len(issues) != c.Writers+c.Readers {
		t.Fatal("accepted cross-config completion")
	}
}
