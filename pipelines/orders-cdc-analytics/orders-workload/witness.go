package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

var validRun = regexp.MustCompile(`^[a-f0-9]{32}$`)

type journal struct {
	file   *os.File
	run    string
	prefix string
}

func caseID() string {
	value := os.Getenv("CASE_ID")
	if value == "" {
		return "initial"
	}
	return value
}
func actorToken(run string) string { return run + "-" + caseID() }

func openJournal(run, mode string) (*journal, error) {
	if !validRun.MatchString(run) {
		return nil, fmt.Errorf("invalid owned fixture UUID")
	}
	file, err := os.OpenFile(filepath.Join("/results", mode+"-"+caseID()+"-"+run+".jsonl"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return nil, err
	}
	return &journal{file: file, run: run, prefix: run + "-" + mode + "-" + caseID()}, nil
}
func (j *journal) append(row map[string]any) error {
	row["run"] = j.run
	row["utc_ns"] = time.Now().UnixNano()
	if err := json.NewEncoder(j.file).Encode(row); err != nil {
		return err
	}
	return j.file.Sync()
}

type receiptRecord struct {
	Event     string `json:"event"`
	Run       string `json:"run"`
	ID        string `json:"operation_id"`
	Operation string `json:"operation"`
	First     int64  `json:"first_id"`
	Last      int64  `json:"last_id"`
	Affected  int64  `json:"affected"`
}
type publication struct {
	intent  receiptRecord
	outcome *receiptRecord
}

func parseWitness(data []byte, run string) ([]publication, error) {
	if !validRun.MatchString(run) || len(data) == 0 || len(data) > 4*1024*1024 || data[len(data)-1] != '\n' {
		return nil, fmt.Errorf("invalid/truncated witness")
	}
	positions := map[string]int{}
	var result []publication
	for _, line := range bytes.Split(data[:len(data)-1], []byte{'\n'}) {
		var row receiptRecord
		decoder := json.NewDecoder(bytes.NewReader(line))
		if err := decoder.Decode(&row); err != nil {
			return nil, err
		}
		if err := decoder.Decode(new(any)); err != io.EOF {
			return nil, fmt.Errorf("trailing witness data")
		}
		if row.Run != run || !strings.HasPrefix(row.ID, run+"-") || row.Operation == "" || row.First < 1 || row.Last < row.First || row.Last > 2000000 || row.Affected < 0 || row.Affected > row.Last-row.First+1 {
			return nil, fmt.Errorf("invalid witness identity/range")
		}
		position, exists := positions[row.ID]
		if row.Event == "intent" {
			if exists {
				return nil, fmt.Errorf("duplicate intent")
			}
			positions[row.ID] = len(result)
			result = append(result, publication{intent: row})
			continue
		}
		if (row.Event != "ack" && row.Event != "ambiguous") || !exists || result[position].outcome != nil {
			return nil, fmt.Errorf("missing intent or duplicate/unknown outcome")
		}
		original := result[position].intent
		if row.Operation != original.Operation || row.First != original.First || row.Last != original.Last {
			return nil, fmt.Errorf("outcome differs from intent")
		}
		result[position].outcome = &row
	}
	return result, nil
}
func receipts(ctx context.Context, pg *sql.DB) error {
	run := os.Getenv("RUN_ID")
	paths, err := filepath.Glob("/results/*-" + run + ".jsonl")
	if err != nil {
		return err
	}
	var acknowledged, unacknowledgedPresent, unacknowledgedAbsent int
	for _, path := range paths {
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		data, err := io.ReadAll(io.LimitReader(file, 4*1024*1024+1))
		file.Close()
		if err != nil {
			return err
		}
		history, err := parseWitness(data, run)
		if err != nil {
			return fmt.Errorf("%s: %w", path, err)
		}
		for _, item := range history {
			intent := item.intent
			var operation string
			var first, last, affected int64
			ack := item.outcome != nil && item.outcome.Event == "ack"
			err = pg.QueryRowContext(ctx, "SELECT operation,first_id,last_id,affected FROM receipts WHERE id=$1", intent.ID).Scan(&operation, &first, &last, &affected)
			if errors.Is(err, sql.ErrNoRows) && !ack {
				unacknowledgedAbsent++
				continue
			}
			if err != nil {
				return err
			}
			if operation != intent.Operation || first != intent.First || last != intent.Last || (ack && affected != item.outcome.Affected) {
				return fmt.Errorf("transactional receipt differs from external witness")
			}
			if ack {
				acknowledged++
			} else {
				unacknowledgedPresent++
			}
		}
	}
	if acknowledged == 0 {
		return fmt.Errorf("no acknowledged source batches")
	}
	emit(map[string]any{"event": "receipts_verified", "acknowledged_batches": acknowledged, "unacknowledged_committed_batches": unacknowledgedPresent, "unacknowledged_absent_batches": unacknowledgedAbsent})
	return nil
}
