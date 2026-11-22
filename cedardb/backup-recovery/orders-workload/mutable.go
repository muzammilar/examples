package main

import (
	"cmp"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"slices"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

type mutableConfig struct {
	Rows    int64  `json:"rows"`
	Pairs   int64  `json:"pairs"`
	Orders  string `json:"orders"`
	Commits string `json:"commits"`
}

func mutableSettings(count int64) (mutableConfig, error) {
	token := os.Getenv("MUTABLE_RUN_ID")
	decoded, err := hex.DecodeString(token)
	if err != nil || len(decoded) != 16 || hex.EncodeToString(decoded) != token {
		return mutableConfig{}, errors.New("MUTABLE_RUN_ID must be 32 lowercase hexadecimal characters")
	}
	available := count / 200 * 100
	pairs, err := number("MUTABLE_PAIRS", min(int64(10000), available-1), 1, available-1)
	if err != nil {
		return mutableConfig{}, err
	}
	schema := "mutable_" + token
	return mutableConfig{count, pairs,
		pgx.Identifier{schema, "mutable_orders"}.Sanitize(),
		pgx.Identifier{schema, "mutation_commits"}.Sanitize()}, nil
}

func mutablePair(index int64) [2]int64 {
	left := index/100*200 + index%100
	return [2]int64{left, left + 100}
}

func mutableOperation(seq int64) string {
	if seq%2 == 1 {
		return "paired-update"
	}
	return "delete-reinsert"
}

type mutableRow struct {
	ID       int64  `json:"id"`
	Customer int64  `json:"customer"`
	Amount   int64  `json:"amount"`
	Payload  string `json:"payload"`
	Revision int64  `json:"revision"`
}

func mutableBody(id, revision int64, right bool) mutableRow {
	row := mutableRow{id, id % 10000, (id % 100000) * 17 % 100000, payload(id), revision}
	if revision != 0 {
		delta := 1 + revision%997
		if right {
			delta = -delta
		}
		row.Amount += delta
		sum := sha256.Sum256([]byte(fmt.Sprintf("mutable:%d:%d", id, revision)))
		row.Payload = payload(id)[:192] + hex.EncodeToString(sum[:])
	}
	return row
}

func rollbackMutable(tx pgx.Tx) {
	bounded, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	// pgx closes a connection when a real rollback fails. Callers discard a
	// connection after any failed mutation; failed commits remain ambiguous.
	_ = tx.Rollback(bounded)
}

func seedMutable(ctx context.Context, conn *pgx.Conn, cfg mutableConfig) error {
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
	if err != nil {
		return err
	}
	defer rollbackMutable(tx)
	for _, sql := range []string{
		"CREATE TABLE " + cfg.Orders + " (id BIGINT PRIMARY KEY, customer INTEGER NOT NULL, amount INTEGER NOT NULL, payload TEXT NOT NULL, revision BIGINT NOT NULL)",
		"CREATE TABLE " + cfg.Commits + " (seq BIGINT PRIMARY KEY, operation TEXT NOT NULL, left_id BIGINT NOT NULL, right_id BIGINT NOT NULL)",
	} {
		if _, err = tx.Exec(ctx, sql); err != nil {
			return err
		}
	}
	tag, err := tx.Exec(ctx, "INSERT INTO "+cfg.Orders+" SELECT id,customer,amount,payload,0 FROM public.orders WHERE id < $1", cfg.Rows)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != cfg.Rows {
		return errors.New("mutable seed row count differs")
	}
	return tx.Commit(ctx)
}

type mutableEvent struct {
	Kind      string           `json:"kind"`
	Request   int64            `json:"request"`
	Seq       int64            `json:"seq"`
	Operation string           `json:"operation,omitempty"`
	Rows      []mutableRow     `json:"rows,omitempty"`
	StartedNS int64            `json:"started_ns"`
	EndedNS   int64            `json:"ended_ns,omitempty"`
	ElapsedNS int64            `json:"elapsed_ns,omitempty"`
	Stages    map[string]int64 `json:"stage_ns,omitempty"`
	Error     string           `json:"error,omitempty"`
}

func mutate(ctx context.Context, conn *pgx.Conn, cfg mutableConfig, seq int64, rows []mutableRow) (bool, map[string]int64, error) {
	stages := make(map[string]int64)
	started := time.Now()
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead})
	stages["begin"] = time.Since(started).Nanoseconds()
	if err != nil {
		return false, stages, err
	}
	defer rollbackMutable(tx)
	var isolation string
	if err := tx.QueryRow(ctx, "SHOW transaction_isolation").Scan(&isolation); err != nil || isolation != "repeatable read" {
		return false, stages, fmt.Errorf("mutable isolation mismatch: %s (%v)", isolation, err)
	}
	exec := func(stage, sql string, affected int64, args ...any) error {
		started := time.Now()
		tag, err := tx.Exec(ctx, sql, args...)
		stages[stage] = time.Since(started).Nanoseconds()
		if err == nil && tag.RowsAffected() != affected {
			return fmt.Errorf("%w: %s affected %d, want %d", errMismatch, stage, tag.RowsAffected(), affected)
		}
		return err
	}
	left, right := rows[0], rows[1]
	if mutableOperation(seq) == "paired-update" {
		err = exec("update", "UPDATE "+cfg.Orders+" SET amount=CASE WHEN id=$1 THEN CAST($3 AS INTEGER) ELSE CAST($4 AS INTEGER) END,payload=CASE WHEN id=$1 THEN $5 ELSE $6 END,revision=$7 WHERE id=$1 OR id=$2", 2,
			left.ID, right.ID, left.Amount, right.Amount, left.Payload, right.Payload, seq)
	} else {
		err = exec("delete", "DELETE FROM "+cfg.Orders+" WHERE id=$1 OR id=$2", 2, left.ID, right.ID)
		if err == nil {
			err = exec("reinsert", "INSERT INTO "+cfg.Orders+" VALUES ($1,$2,$3,$4,$5),($6,$7,$8,$9,$5)", 2,
				left.ID, left.Customer, left.Amount, left.Payload, seq, right.ID, right.Customer, right.Amount, right.Payload)
		}
	}
	if err == nil {
		err = exec("receipt", "INSERT INTO "+cfg.Commits+" VALUES ($1,$2,$3,$4)", 1, seq, mutableOperation(seq), left.ID, right.ID)
	}
	if err != nil {
		return true, stages, err
	}
	started = time.Now()
	err = tx.Commit(ctx)
	stages["commit"] = time.Since(started).Nanoseconds()
	return true, stages, err
}

func readMutable(ctx context.Context, conn *pgx.Conn, cfg mutableConfig) (int64, error) {
	tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return 0, err
	}
	defer rollbackMutable(tx)
	var seq int64
	if err = tx.QueryRow(ctx, "SELECT coalesce(max(seq),0) FROM "+cfg.Commits).Scan(&seq); err != nil {
		return 0, err
	}
	index := int64(0)
	if seq > 0 {
		index = (seq - 1) / 2 % cfg.Pairs
	}
	recent := mutablePair(index)
	old := mutablePair(cfg.Rows/200*100 - 1)
	rows, err := tx.Query(ctx, "SELECT id,customer,amount,payload,revision FROM "+cfg.Orders+" WHERE id=$1 OR id=$2 OR id=$3 OR id=$4 ORDER BY id", recent[0], recent[1], old[0], old[1])
	if err != nil {
		return 0, err
	}
	expected := []mutableRow{mutableBody(recent[0], seq, false), mutableBody(recent[1], seq, true), mutableBody(old[0], 0, false), mutableBody(old[1], 0, true)}
	slices.SortFunc(expected, func(a, b mutableRow) int { return cmp.Compare(a.ID, b.ID) })
	seen := 0
	for rows.Next() {
		var actual mutableRow
		if err = rows.Scan(&actual.ID, &actual.Customer, &actual.Amount, &actual.Payload, &actual.Revision); err != nil {
			rows.Close()
			return 0, err
		}
		if seen >= len(expected) || actual != expected[seen] {
			rows.Close()
			return 0, fmt.Errorf("%w: mutable snapshot row %d differs", errMismatch, actual.ID)
		}
		seen++
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return 0, err
	}
	if seen != len(expected) {
		return 0, fmt.Errorf("%w: mutable snapshot lacks rows", errMismatch)
	}
	return seq, tx.Commit(ctx)
}

func mutableActor(ctx context.Context, mode, path string, cfg mutableConfig) error {
	journal, err := createJournal(path)
	if err != nil {
		return err
	}
	defer journal.Close()
	var conn *pgx.Conn
	defer func() {
		if conn != nil {
			closeConnection(conn)
		}
	}()
	rate, err := number("RATE", 50, 1, 1000)
	if mode == "mutable-reader" {
		rate, err = number("MUTABLE_READ_RATE", 20, 1, 1000)
	}
	if err != nil {
		return err
	}
	period := time.NewTicker(time.Second / time.Duration(rate))
	defer period.Stop()
	lastSeen := int64(0)
	for seq := int64(1); seq <= 1000000; seq++ {
		select {
		case <-ctx.Done():
			return nil
		case <-period.C:
		}
		index := (seq - 1) / 2 % cfg.Pairs
		pair := mutablePair(index)
		values := []mutableRow{mutableBody(pair[0], seq, false), mutableBody(pair[1], seq, true)}
		if mode == "mutable-writer" {
			if err = record(journal, mutableEvent{Kind: "intent", Request: seq, Seq: seq, Operation: mutableOperation(seq), Rows: values, StartedNS: time.Now().UnixNano()}); err != nil {
				return err
			}
		}
		started := time.Now()
		bounded, cancel := context.WithTimeout(ctx, 3*time.Second)
		var opErr error
		if conn == nil {
			conn, opErr = connect(bounded)
		}
		submitted := false
		value := mutableEvent{Kind: "ok", Request: seq, Seq: seq, StartedNS: started.UnixNano()}
		value.Operation = "snapshot-recent-and-historical"
		if mode == "mutable-writer" {
			value.Operation = mutableOperation(seq)
		}
		if opErr == nil {
			if mode == "mutable-writer" {
				submitted, value.Stages, opErr = mutate(bounded, conn, cfg, seq, values)
			} else {
				value.Seq, opErr = readMutable(bounded, conn, cfg)
				if opErr == nil && value.Seq < lastSeen {
					opErr = fmt.Errorf("%w: committed watermark regressed", errMismatch)
				}
				if opErr == nil {
					lastSeen = value.Seq
				}
			}
		}
		cancel()
		value.EndedNS = time.Now().UnixNano()
		value.ElapsedNS = time.Since(started).Nanoseconds()
		if opErr != nil {
			value.Kind = "fail"
			if submitted {
				value.Kind = "ambiguous"
			}
			if errors.Is(opErr, errMismatch) {
				value.Kind = "mismatch"
			}
			value.Error = opErr.Error()
			if conn != nil {
				closeConnection(conn)
				conn = nil
			}
		}
		if err = record(journal, value); err != nil {
			return err
		}
		if value.Kind == "mismatch" {
			return opErr
		}
	}
	return errors.New("mutable actor exhausted bounded sequence space")
}

func runMutable(ctx context.Context, mode string, count int64) error {
	cfg, err := mutableSettings(count)
	if err != nil {
		return err
	}
	if err = json.NewEncoder(os.Stdout).Encode(map[string]any{"kind": "mutable-config", "config": cfg, "mode": mode}); err != nil {
		return err
	}
	if mode == "mutable-writer" || mode == "mutable-reader" {
		if len(os.Args) != 3 {
			return errors.New("mutable actor needs an exclusive external journal path")
		}
		return mutableActor(ctx, mode, os.Args[2], cfg)
	}
	bounded, cancel := context.WithTimeout(ctx, 240*time.Second)
	defer cancel()
	conn, err := connect(bounded)
	if err != nil {
		return err
	}
	defer closeConnection(conn)
	switch mode {
	case "mutable-controls":
		return mutableControls(bounded, conn, cfg)
	case "mutable-seed":
		return seedMutable(bounded, conn, cfg)
	case "mutable-analytics":
		return analyticsOn(ctx, conn, cfg.Orders, count)
	case "mutable-export", "mutable-receipts":
		if len(os.Args) != 3 {
			return errors.New("mutable export needs a new external file path")
		}
		file, err := createJournal(os.Args[2])
		if err != nil {
			return err
		}
		defer file.Close()
		query := "SELECT id,customer,amount,payload,revision FROM " + cfg.Orders + " ORDER BY id"
		if mode == "mutable-receipts" {
			query = "SELECT seq,operation,left_id,right_id FROM " + cfg.Commits + " ORDER BY seq"
		}
		_, err = conn.PgConn().CopyTo(bounded, file, "COPY ("+query+") TO STDOUT WITH (FORMAT CSV, HEADER TRUE)")
		if err != nil {
			return err
		}
		return file.Sync()
	default:
		return errors.New("unknown mutable mode")
	}
}

func mutableControls(ctx context.Context, conn *pgx.Conn, cfg mutableConfig) error {
	var count int64
	if err := conn.QueryRow(ctx, "SELECT count(*) FROM "+cfg.Commits).Scan(&count); err != nil {
		return err
	}
	if count != 0 {
		return errors.New("atomicity controls require a fresh, unmodified case")
	}
	for _, probe := range []struct {
		readonly bool
		code     string
	}{{true, "25006"}, {false, "22012"}} {
		access := pgx.ReadWrite
		if probe.readonly {
			access = pgx.ReadOnly
		}
		tx, err := conn.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: access})
		if err != nil {
			return err
		}
		var statementErr error
		if probe.readonly {
			_, statementErr = tx.Exec(ctx, "UPDATE "+cfg.Orders+" SET amount=amount+1 WHERE id=0")
		} else {
			tag, updateErr := tx.Exec(ctx, "UPDATE "+cfg.Orders+" SET amount=amount+1 WHERE id=0")
			if updateErr != nil {
				rollbackMutable(tx)
				return updateErr
			}
			if tag.RowsAffected() != 1 {
				rollbackMutable(tx)
				return errors.New("atomicity probe did not modify exactly one row")
			}
			_, statementErr = tx.Exec(ctx, "SELECT 1/0")
		}
		var native *pgconn.PgError
		expected := errors.As(statementErr, &native) && native.Code == probe.code
		if err = tx.Rollback(ctx); err != nil {
			return err
		}
		if !expected {
			return fmt.Errorf("expected native SQLSTATE %s, got %v", probe.code, statementErr)
		}
		seq, readErr := readMutable(ctx, conn, cfg)
		if readErr != nil {
			return readErr
		}
		if seq != 0 {
			return errors.New("rollback probe created a commit receipt")
		}
	}
	return json.NewEncoder(os.Stdout).Encode(map[string]any{"kind": "mutable-negative-controls", "readonly_sqlstate": "25006", "rollback_sqlstate": "22012", "passed": true})
}
