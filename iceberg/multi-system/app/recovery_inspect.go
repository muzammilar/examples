package main

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

type recoveryInspectionProof struct {
	Rows     int64  `json:"rows"`
	SeedRows int64  `json:"seed_rows"`
	Batches  int    `json:"batches"`
	BodyHash string `json:"body_sha256"`
}

type recoveryInspectionPin struct {
	Observation string                  `json:"observation_id"`
	Snapshot    recoverySnapshot        `json:"snapshot"`
	Proof       recoveryInspectionProof `json:"proof"`
}

type recoveryInspectionCandidate struct {
	URI     string                  `json:"uri"`
	Parquet recoveryParquetFile     `json:"parquet"`
	Proof   recoveryInspectionProof `json:"proof"`
}

type recoverySnapshotOperation struct {
	ID        int64  `json:"snapshot_id"`
	Operation string `json:"operation"`
}

type recoveryInspection struct {
	Passed     bool                         `json:"passed"`
	RunID      string                       `json:"run_id"`
	ConfigHash string                       `json:"config_sha256"`
	ID         string                       `json:"observation_id"`
	Phase      string                       `json:"phase"`
	Started    int64                        `json:"started_ns"`
	Ended      int64                        `json:"ended_ns"`
	Catalog    recoverySnapshot             `json:"catalog"`
	Operations []recoverySnapshotOperation  `json:"operations"`
	Pin        recoveryInspectionPin        `json:"pin"`
	Candidate  *recoveryInspectionCandidate `json:"candidate,omitempty"`
	Error      string                       `json:"error,omitempty"`
	Before     map[string]any               `json:"native_before"`
	After      map[string]any               `json:"native_after"`
}

// A live snapshot need not include acknowledgements made after it was selected.
// Every included row still needs an exact durable intent and deterministic body.
// Complete snapshots require all seed rows and whole batches; an individual
// physical output file can legitimately split a batch across file boundaries.
func recoveryInspectRows(rows recoveryRowStream, c recoveryConfig, attempts []recoveryAttempt, complete bool) (recoveryInspectionProof, error) {
	var proof recoveryInspectionProof
	counts := make([]int64, len(attempts))
	hash := newRecoveryBodyHash()
	previous := int64(-1)
	for rows.Next() {
		row, err := recoveryScan(rows)
		if err != nil {
			return proof, err
		}
		if row.ID < 0 || row.ID <= previous {
			return proof, errors.New("duplicate or unordered inspected row")
		}
		index := sort.Search(len(attempts), func(i int) bool { return attempts[i].Intent.Lo > row.ID }) - 1
		if index < 0 || row.ID >= attempts[index].Intent.Hi {
			return proof, errors.New("inspected row has no durable intent")
		}
		if row != recoveryExpected(c, attempts[index].Intent, row.ID) {
			return proof, fmt.Errorf("wrong inspected body at ID %d", row.ID)
		}
		if err = hash.write(row); err != nil {
			return proof, err
		}
		counts[index]++
		proof.Rows++
		if row.Writer == -1 {
			proof.SeedRows++
		}
		previous = row.ID
	}
	if err := rows.Err(); err != nil {
		return proof, err
	}
	for index, count := range counts {
		if count == 0 {
			continue
		}
		proof.Batches++
		if complete && count != attempts[index].Intent.Hi-attempts[index].Intent.Lo {
			return proof, errors.New("partial batch in committed pinned snapshot")
		}
	}
	if proof.Rows == 0 || (complete && proof.SeedRows != c.Rows) {
		return proof, errors.New("empty output or missing seed in pinned snapshot")
	}
	proof.BodyHash = hash.finish()
	return proof, nil
}

func recoveryInspectionDataPrefix(c recoveryConfig, snapshot recoverySnapshot) (string, error) {
	path, err := recoveryWarehouseURI(snapshot.Metadata)
	want := "/lab/" + c.Table + "-" + snapshot.UUID + "/"
	if err != nil || !recoveryUUID.MatchString(snapshot.UUID) || snapshot.Snapshot <= 0 || !strings.HasPrefix(path, want+"metadata/") {
		return "", errors.New("snapshot does not identify this owned table's metadata")
	}
	return "s3://warehouse" + want + "data/", nil
}

func recoveryInspectionOperations(directory string) ([]recoverySnapshotOperation, error) {
	data, err := os.ReadFile(filepath.Join(directory, "catalog.json"))
	if err != nil {
		return nil, err
	}
	var record struct {
		Available bool `json:"available"`
		Table     struct {
			Metadata struct {
				Snapshots []struct {
					ID      int64 `json:"snapshot-id"`
					Summary struct {
						Operation string `json:"operation"`
					} `json:"summary"`
				} `json:"snapshots"`
			} `json:"metadata"`
		} `json:"load_table"`
	}
	if err = json.Unmarshal(data, &record); err != nil {
		return nil, err
	}
	if !record.Available || len(record.Table.Metadata.Snapshots) == 0 {
		return nil, errors.New("catalog snapshot history unavailable")
	}
	var result []recoverySnapshotOperation
	seen := map[int64]bool{}
	for _, snapshot := range record.Table.Metadata.Snapshots {
		if snapshot.ID <= 0 || snapshot.Summary.Operation == "" || seen[snapshot.ID] {
			return nil, errors.New("invalid or duplicate catalog snapshot history")
		}
		seen[snapshot.ID] = true
		result = append(result, recoverySnapshotOperation{ID: snapshot.ID, Operation: snapshot.Summary.Operation})
	}
	return result, nil
}

func recoveryInspect() (returned error) {
	root, err := recoveryRoot()
	if err != nil {
		return err
	}
	c, digest, err := recoveryLoad(root)
	if err != nil {
		return err
	}
	id, phase := os.Getenv("RECOVERY_OBSERVATION_ID"), os.Getenv("RECOVERY_INSPECTION_PHASE")
	if !recoveryUUID.MatchString(id) || (phase != "before" && phase != "candidate" && phase != "interrupted" && phase != "recovered") {
		return errors.New("explicit inspection UUID and phase required")
	}
	pinID, candidateURI := os.Getenv("RECOVERY_PIN_OBSERVATION"), os.Getenv("RECOVERY_CANDIDATE_URI")
	if (phase == "before" && pinID != "") || (phase != "before" && !recoveryUUID.MatchString(pinID)) || (phase == "candidate") != (candidateURI != "") {
		return errors.New("inspection pin/candidate arguments differ from phase")
	}
	parent := filepath.Join(root, "observations")
	if err = os.MkdirAll(parent, 0755); err != nil {
		return err
	}
	directory := filepath.Join(parent, id)
	if err = os.Mkdir(directory, 0755); err != nil {
		return err
	}
	if err = recoverySyncDir(parent); err != nil {
		return err
	}
	report := recoveryInspection{RunID: c.RunID, ConfigHash: digest, ID: id, Phase: phase, Started: time.Now().UnixNano(), Before: recoveryNativeCounters()}
	var db *sql.DB
	defer func() {
		if db != nil {
			returned = errors.Join(returned, db.Close())
		}
		report.Ended = time.Now().UnixNano()
		report.Passed = returned == nil
		if returned != nil {
			report.Error = returned.Error()
		}
		report.After = recoveryNativeCounters()
		returned = errors.Join(returned, recoverySave(filepath.Join(directory, "inspection.json"), report))
	}()
	base, stop := recoveryContext(root)
	defer stop()
	ctx, cancel := context.WithTimeout(base, time.Duration(c.VerifyTimeoutSeconds)*time.Second)
	defer cancel()
	if err = recoveryCatalogRecord(ctx, directory, c, digest, "catalog"); err != nil {
		return err
	}
	report.Catalog, err = recoveryCatalogState(directory, "catalog")
	if err != nil {
		return err
	}
	prefix, err := recoveryInspectionDataPrefix(c, report.Catalog)
	if err != nil {
		return err
	}
	report.Operations, err = recoveryInspectionOperations(directory)
	if err != nil {
		return err
	}
	if phase == "before" {
		report.Pin = recoveryInspectionPin{Observation: id, Snapshot: report.Catalog}
	} else {
		data, e := os.ReadFile(filepath.Join(parent, pinID, "inspection.json"))
		if e != nil {
			return e
		}
		var original recoveryInspection
		if e = json.Unmarshal(data, &original); e != nil {
			return e
		}
		if !original.Passed || original.Phase != "before" || original.ID != pinID || original.RunID != c.RunID || original.ConfigHash != digest || original.Pin.Observation != pinID || original.Catalog.UUID != report.Catalog.UUID || original.Pin.Snapshot != original.Catalog {
			return errors.New("invalid or cross-run pinned inspection")
		}
		report.Pin = original.Pin
	}
	// Read intents after selecting metadata: every visible commit was preceded
	// by its fsynced intent. Later acknowledgements need not occur in this pin.
	attempts, err := recoveryReadAttempts(root, c, digest, true)
	if err != nil {
		return err
	}
	db, err = recoveryDuck(ctx, c)
	if err != nil {
		return err
	}
	query := "SELECT " + strings.Join(recoveryColumns, ",") + " FROM iceberg_scan(?,snapshot_from_id=CAST(? AS UBIGINT)) ORDER BY event_id"
	rows, err := db.QueryContext(ctx, query, report.Pin.Snapshot.Metadata, report.Pin.Snapshot.Snapshot)
	if err != nil {
		return err
	}
	proof, readErr := recoveryInspectRows(rows, c, attempts, true)
	if err = errors.Join(readErr, rows.Close()); err != nil {
		return err
	}
	if phase != "before" && proof != report.Pin.Proof {
		return errors.New("pinned snapshot body changed")
	}
	report.Pin.Proof = proof
	if phase == "candidate" {
		if _, err = recoveryWarehouseURI(candidateURI); err != nil {
			return err
		}
		if !strings.HasPrefix(candidateURI, prefix) || !strings.HasSuffix(candidateURI, ".parquet") {
			return errors.New("candidate is outside the exact owned data prefix")
		}
		var count int64
		if err = db.QueryRowContext(ctx, "SELECT num_rows FROM parquet_file_metadata(?)", candidateURI).Scan(&count); err != nil {
			return err
		}
		file, err := recoveryParquetMetadata(ctx, db, candidateURI, count)
		if err != nil {
			return err
		}
		rows, err := db.QueryContext(ctx, "SELECT "+strings.Join(recoveryColumns, ",")+" FROM read_parquet(?) ORDER BY event_id", candidateURI)
		if err != nil {
			return err
		}
		body, readErr := recoveryInspectRows(rows, c, attempts, false)
		if err = errors.Join(readErr, rows.Close()); err != nil {
			return err
		}
		if body.Rows != file.Rows {
			return errors.New("candidate footer/body count mismatch")
		}
		report.Candidate = &recoveryInspectionCandidate{URI: candidateURI, Parquet: file, Proof: body}
	}
	return nil
}
