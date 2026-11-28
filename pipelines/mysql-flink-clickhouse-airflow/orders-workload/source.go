package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"
)

func appendRecord(file *os.File, row any) error {
	data, err := json.Marshal(row)
	if err != nil {
		return err
	}
	if _, err = file.Write(append(data, '\n')); err != nil {
		return err
	}
	return file.Sync()
}

func journal(name string) (*os.File, error) {
	root, err := casePath()
	if err != nil {
		return nil, err
	}
	return os.OpenFile(root+"/"+name+".jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY|os.O_APPEND, 0600)
}

func seed(ctx context.Context, db *sql.DB, n, customers int64) error {
	id, err := runID()
	if err != nil {
		return err
	}
	file, err := journal("seed")
	if err != nil {
		return err
	}
	defer file.Close()
	intent := map[string]any{"event": "intent", "operation": "seed", "receipt": id, "orders": n, "customers": customers, "generator": "integer-margin-v1"}
	if err = appendRecord(file, intent); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, 180*time.Second)
	defer cancel()
	began := time.Now()
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for first := int64(1); first <= customers; first += 1000 {
		values, args := []string{}, []any{}
		for i := first; i <= customers && i < first+1000; i++ {
			values = append(values, "(?,?,?,1)")
			args = append(args, i, fmt.Sprintf("r%d", i%10), (i%6)*100)
		}
		if _, err = tx.ExecContext(ctx, "INSERT INTO customers VALUES "+strings.Join(values, ","), args...); err != nil {
			return err
		}
	}
	for first := int64(1); first <= n; first += 1000 {
		values, args := []string{}, []any{}
		for i := first; i <= n && i < first+1000; i++ {
			values = append(values, "(?,?,?,?,1,0,?)")
			args = append(args, i, (i-1)%customers+1, 1000+i%10000, 600+i%500, 1700000000+i%86400)
		}
		if _, err = tx.ExecContext(ctx, "INSERT INTO orders(id,customer_id,amount_cents,cost_cents,revision,cancelled,event_second) VALUES "+strings.Join(values, ","), args...); err != nil {
			return err
		}
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO receipts VALUES (?,'seed',1,?,?)", id, n, n); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		intent["event"] = "ambiguous"
		intent["error"] = err.Error()
		_ = appendRecord(file, intent)
		return err
	}
	intent["event"] = "ack"
	intent["duration_ms"] = float64(time.Since(began)) / float64(time.Millisecond)
	if err = appendRecord(file, intent); err != nil {
		return err
	}
	emit(intent)
	return nil
}

func writer(ctx context.Context, db *sql.DB, n, customers int64) error {
	id, err := runID()
	if err != nil {
		return err
	}
	root, err := casePath()
	if err != nil {
		return err
	}
	file, err := journal("witness")
	if err != nil {
		return err
	}
	defer file.Close()
	var start int64
	if err = db.QueryRowContext(ctx, "SELECT COALESCE(MAX(id),?) FROM orders", n).Scan(&start); err != nil {
		return err
	}
	deadline := time.Now().Add(600 * time.Second)
	for seq := int64(1); time.Now().Before(deadline) && ctx.Err() == nil && !exists(root+"/stop"); seq++ {
		orderID, customer, amount, cost, second := start+seq, (seq-1)%customers+1, 1000+3*seq, 500+seq%300, 1700000000+seq
		sum := sha256.Sum256([]byte(fmt.Sprintf("%d:%d:%d:%d:%d", orderID, customer, amount, cost, second)))
		receipt := fmt.Sprintf("%s:%d", id, seq)
		row := map[string]any{"event": "intent", "sequence": seq, "receipt": receipt, "id": orderID, "customer_id": customer, "amount_cents": amount, "cost_cents": cost, "event_second": second, "sha256": hex.EncodeToString(sum[:]), "phase": phase(), "utc_start_ns": time.Now().UnixNano()}
		if err = appendRecord(file, row); err != nil {
			return err
		}
		began := time.Now()
		opctx, cancel := context.WithTimeout(ctx, 30*time.Second)
		tx, e := db.BeginTx(opctx, nil)
		if e == nil {
			_, e = tx.ExecContext(opctx, "INSERT INTO orders(id,customer_id,amount_cents,cost_cents,revision,cancelled,event_second) VALUES (?,?,?,?,1,0,?)", orderID, customer, amount, cost, second)
			if e == nil {
				_, e = tx.ExecContext(opctx, "UPDATE orders SET amount_cents=?,revision=revision+1,cancelled=? WHERE id=?", amount, seq%5 == 0, (seq-1)%10000+1)
			}
			if e == nil && seq%7 == 0 {
				_, e = tx.ExecContext(opctx, "DELETE FROM orders WHERE id=?", 20000+seq%10000)
			}
			if e == nil && seq%20 == 0 {
				_, e = tx.ExecContext(opctx, "UPDATE customers SET region=?,discount_bps=?,revision=revision+1 WHERE id=?", fmt.Sprintf("m%d", seq%7), seq%4000, (seq/20-1)%100+1)
			}
			if e == nil {
				_, e = tx.ExecContext(opctx, "INSERT INTO receipts VALUES (?,'live',?,?,1)", receipt, orderID, orderID)
			}
			if e == nil {
				e = tx.Commit()
			} else {
				tx.Rollback()
			}
		}
		cancel()
		row["event"] = "ack"
		row["duration_ms"] = float64(time.Since(began)) / float64(time.Millisecond)
		row["utc_end_ns"] = time.Now().UnixNano()
		if e != nil {
			row["event"] = "ambiguous"
			row["error"] = e.Error()
		}
		if err = appendRecord(file, row); err != nil {
			return err
		}
		emit(row)
		if e == nil {
			if err = saveJSON("latest-ack.json", row); err != nil {
				return err
			}
		}
		if e != nil {
			return e
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(100 * time.Millisecond):
		}
	}
	return nil
}

func correct(ctx context.Context, db *sql.DB) error {
	id, err := runID()
	if err != nil {
		return err
	}
	file, err := journal("correction")
	if err != nil {
		return err
	}
	defer file.Close()
	row := map[string]any{"event": "intent", "operation": "shipping-correction", "receipt": id, "first_id": 10001, "last_id": 11000, "rows": 1000}
	if err = appendRecord(file, row); err != nil {
		return err
	}
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	result, err := tx.ExecContext(ctx, "UPDATE orders SET shipping_cents=25+(id%100),revision=revision+1 WHERE id BETWEEN 10001 AND 11000")
	if err != nil {
		return err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if n != 1000 {
		return errors.New("wrong correction range")
	}
	if _, err = tx.ExecContext(ctx, "INSERT INTO receipts VALUES (?,'shipping',10001,11000,1000)", id); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		row["event"] = "ambiguous"
		row["error"] = err.Error()
		_ = appendRecord(file, row)
		return err
	}
	row["event"] = "ack"
	if err = appendRecord(file, row); err != nil {
		return err
	}
	emit(row)
	return nil
}
