// The historical batch owns its source snapshot and durable publication receipts.
package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/csv"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"syscall"
	"time"

	_ "github.com/go-sql-driver/mysql"
	_ "github.com/jackc/pgx/v5/stdlib"
)

var valid = regexp.MustCompile(`^[a-f0-9]{32}$`)

const columns = "order_id,customer_id,amount_cents,region,order_revision,customer_revision,event_second,version"

func emit(v any) {
	if err := json.NewEncoder(os.Stdout).Encode(v); err != nil {
		panic(err)
	}
}
func save(path string, v any) error {
	b, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		return err
	}
	f, err := os.OpenFile(path, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer f.Close()
	if _, err = f.Write(append(b, '\n')); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	d, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}
func dbs() (*sql.DB, *sql.DB, error) {
	p, err := sql.Open("pgx", "postgres://app:local-app-only@postgres:5432/orders?sslmode=disable&connect_timeout=5")
	if err != nil {
		return nil, nil, err
	}
	d, err := sql.Open("mysql", "root@tcp(fe1:9030)/pipeline?timeout=5s&readTimeout=60s&writeTimeout=30s&interpolateParams=true&enable_sql_cache=false&enable_query_cache=false")
	if err != nil {
		p.Close()
		return nil, nil, err
	}
	for _, db := range []*sql.DB{p, d} {
		db.SetMaxOpenConns(1)
		db.SetMaxIdleConns(1)
	}
	return p, d, nil
}
func export(ctx context.Context, pg *sql.DB, dir, run, caseID string, n int) error {
	// Match the parent witness schema so its independent receipt checker covers this mutation.
	j, err := os.OpenFile("/results/historical-"+caseID+"-"+run+".jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
	if err != nil {
		return err
	}
	defer j.Close()
	id := run + "-historical-" + caseID
	record := map[string]any{"event": "intent", "operation_id": id, "operation": "historical-correction", "first_id": 1, "last_id": n, "run": run, "utc_ns": time.Now().UnixNano()}
	appendEvent := func() error {
		if e := json.NewEncoder(j).Encode(record); e != nil {
			return e
		}
		return j.Sync()
	}
	if err = appendEvent(); err != nil {
		return err
	}
	started := time.Now()
	mutationErr := func() error {
		tx, e := pg.BeginTx(ctx, nil)
		if e != nil {
			return e
		}
		defer tx.Rollback()
		r, e := tx.ExecContext(ctx, "UPDATE orders SET revision=revision+1,amount_cents=amount_cents+500,event_second=1680000000+id%86400 WHERE id<=$1", n)
		if e != nil {
			return e
		}
		count, e := r.RowsAffected()
		if e != nil {
			return e
		}
		if count != int64(n) {
			return fmt.Errorf("correction affected %d expected %d", count, n)
		}
		if _, e = tx.ExecContext(ctx, "INSERT INTO receipts(id,operation,first_id,last_id,affected) VALUES($1,'historical-correction',1,$2,$2)", id, n); e != nil {
			return e
		}
		return tx.Commit()
	}()
	record["duration_ms"] = float64(time.Since(started).Nanoseconds()) / 1e6
	record["event"] = "ack"
	record["affected"] = n
	if mutationErr != nil {
		record["event"] = "ambiguous"
		record["error"] = mutationErr.Error()
	}
	if err = appendEvent(); err != nil {
		return err
	}
	if mutationErr != nil {
		return mutationErr
	}
	tx, err := pg.BeginTx(ctx, &sql.TxOptions{Isolation: sql.LevelRepeatableRead, ReadOnly: true})
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var snapshot, lsn string
	if err = tx.QueryRowContext(ctx, "SELECT txid_current_snapshot()::text,pg_current_wal_lsn()::text").Scan(&snapshot, &lsn); err != nil {
		return err
	}
	counts := map[string]int{}
	for _, item := range []struct{ name, query string }{
		{"orders", "SELECT id,customer_id,amount_cents,revision,cancelled,event_second FROM orders WHERE id<=" + strconv.Itoa(n) + " ORDER BY id"},
		{"customers", "SELECT id,region,revision FROM customers ORDER BY id"},
	} {
		rows, e := tx.QueryContext(ctx, item.query)
		if e != nil {
			return e
		}
		names, e := rows.Columns()
		if e != nil {
			rows.Close()
			return e
		}
		f, e := os.OpenFile(filepath.Join(dir, item.name+".csv"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0644)
		if e != nil {
			rows.Close()
			return e
		}
		writer := csv.NewWriter(f)
		for rows.Next() {
			values := make([]any, len(names))
			ptr := make([]any, len(names))
			for i := range values {
				ptr[i] = &values[i]
			}
			if e = rows.Scan(ptr...); e != nil {
				break
			}
			fields := make([]string, len(names))
			for i, v := range values {
				if b, ok := v.([]byte); ok {
					fields[i] = string(b)
				} else {
					fields[i] = fmt.Sprint(v)
				}
			}
			if e = writer.Write(fields); e != nil {
				break
			}
			counts[item.name]++
		}
		if e == nil {
			e = rows.Err()
		}
		rows.Close()
		writer.Flush()
		if e == nil {
			e = writer.Error()
		}
		if e == nil {
			e = f.Sync()
		}
		f.Close()
		if e != nil {
			return e
		}
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	return save(filepath.Join(dir, "snapshot.json"), map[string]any{"fixture": run, "case": caseID, "counts": counts, "pg_snapshot": snapshot, "wal_lsn_observation": lsn, "scope": "same repeatable-read snapshot; WAL observation is not a logical CDC cut"})
}
func request(ctx context.Context, endpoint string, body io.Reader, headers map[string]string) (map[string]any, error) {
	req, err := http.NewRequestWithContext(ctx, "PUT", endpoint, body)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth("root", "")
	if f, ok := body.(*os.File); ok {
		info, e := f.Stat()
		if e != nil {
			return nil, e
		}
		req.ContentLength = info.Size()
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	// No proxy/default endpoint or redirect can escape the example-owned BE.
	client := http.Client{Timeout: 90 * time.Second, Transport: &http.Transport{Proxy: nil, DialContext: (&net.Dialer{Timeout: 5 * time.Second}).DialContext, ResponseHeaderTimeout: 80 * time.Second, ExpectContinueTimeout: time.Second}, CheckRedirect: func(*http.Request, []*http.Request) error { return fmt.Errorf("unexpected redirect") }}
	response, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 1024*1024))
	if err != nil {
		return nil, err
	}
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("HTTP %d: %s", response.StatusCode, data)
	}
	var row map[string]any
	if err = json.Unmarshal(data, &row); err != nil {
		return nil, err
	}
	return row, nil
}
func prepared(ctx context.Context, dir, caseID string) error {
	bodyPath := filepath.Join(dir, "joined.csv")
	b, err := os.ReadFile(bodyPath)
	if err != nil {
		return err
	}
	sum := sha256.Sum256(b)
	input, err := os.Open(bodyPath)
	if err != nil {
		return err
	}
	reader := csv.NewReader(input)
	expectedRows := float64(0)
	for {
		r, e := reader.Read()
		if e == io.EOF {
			break
		}
		if e != nil {
			input.Close()
			return e
		}
		if len(r) != 8 {
			input.Close()
			return fmt.Errorf("invalid load field count")
		}
		expectedRows++
	}
	input.Close()
	if expectedRows == 0 {
		return fmt.Errorf("empty historical batch")
	}
	for _, table := range []string{"checkpointed", "asynchronous"} {
		label := "historical_" + caseID + "_" + table
		f, e := os.Open(bodyPath)
		if e != nil {
			return e
		}
		started := time.Now()
		row, e := request(ctx, "http://be1:8040/api/pipeline/"+table+"/_stream_load", f, map[string]string{"Expect": "100-continue", "label": label, "columns": columns, "column_separator": ",", "format": "csv", "strict_mode": "true", "max_filter_ratio": "0", "two_phase_commit": "true", "timeout": "120"})
		f.Close()
		if e != nil {
			return e
		}
		if err = save(filepath.Join(dir, "prepare-"+table+".json"), map[string]any{"label": label, "body_sha256": hex.EncodeToString(sum[:]), "response": row, "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6}); err != nil {
			return err
		}
		if row["Status"] != "Success" || row["NumberTotalRows"] != expectedRows || row["NumberLoadedRows"] != expectedRows || row["NumberFilteredRows"] != float64(0) {
			return fmt.Errorf("prepare did not load every exact body: %v", row)
		}
	}
	if err = save(filepath.Join(dir, "prepared.json"), map[string]any{"event": "precommitted", "utc_ns": time.Now().UnixNano()}); err != nil {
		return err
	}
	emit(map[string]any{"event": "precommitted", "case": caseID})
	// Deliberate phase gate: the controller kills this owned publisher before commit.
	<-ctx.Done()
	return ctx.Err()
}
func transactions(ctx context.Context, doris *sql.DB, dir string, commit bool) error {
	for _, table := range []string{"checkpointed", "asynchronous"} {
		b, err := os.ReadFile(filepath.Join(dir, "prepare-"+table+".json"))
		if err != nil {
			return err
		}
		var saved struct {
			Label string `json:"label"`
		}
		if err = json.Unmarshal(b, &saved); err != nil {
			return err
		}
		if commit {
			started := time.Now()
			reply, e := request(ctx, "http://be1:8040/api/pipeline/"+table+"/_stream_load_2pc", nil, map[string]string{"label": saved.Label, "txn_operation": "commit"})
			if e != nil {
				return e
			}
			emit(map[string]any{"event": "commit_response", "table": table, "label": saved.Label, "response": reply, "duration_ms": float64(time.Since(started).Nanoseconds()) / 1e6})
			// An already-committed retry can return a rejection. Native VISIBLE state below is the gate.
		}
		deadline := time.Now().Add(60 * time.Second)
		for {
			result, e := transactionState(ctx, doris, saved.Label)
			if e != nil {
				return e
			}
			emit(map[string]any{"event": "transaction", "table": table, "native": result})
			expected := "PRECOMMITTED"
			if commit {
				expected = "VISIBLE"
			}
			if result["TransactionStatus"] == expected {
				break
			}
			if !commit || result["TransactionStatus"] != "COMMITTED" || time.Now().After(deadline) {
				return fmt.Errorf("expected %s, got %v", expected, result)
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(250 * time.Millisecond):
			}
		}
	}
	return nil
}
func transactionState(ctx context.Context, db *sql.DB, label string) (map[string]string, error) {
	rows, err := db.QueryContext(ctx, "SHOW TRANSACTION FROM pipeline WHERE LABEL = '"+label+"'")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	names, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	if !rows.Next() {
		return nil, fmt.Errorf("missing transaction: %v", rows.Err())
	}
	values := make([]sql.RawBytes, len(names))
	ptr := make([]any, len(names))
	for i := range values {
		ptr[i] = &values[i]
	}
	if err = rows.Scan(ptr...); err != nil {
		return nil, err
	}
	result := map[string]string{}
	for i, v := range values {
		result[names[i]] = string(v)
	}
	if rows.Next() {
		return nil, fmt.Errorf("duplicate transaction label")
	}
	return result, rows.Err()
}

func run() error {
	if len(os.Args) != 2 {
		return fmt.Errorf("one mode required")
	}
	mode := os.Args[1]
	if mode == "idle" {
		for {
			time.Sleep(time.Hour)
		}
	}
	run, caseID := os.Getenv("RUN_ID"), os.Getenv("CASE_ID")
	if !valid.MatchString(run) || !valid.MatchString(caseID) {
		return fmt.Errorf("owned UUIDs required")
	}
	dir := "/results/historical/" + caseID
	pg, doris, err := dbs()
	if err != nil {
		return err
	}
	defer pg.Close()
	defer doris.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()
	switch mode {
	case "export":
		n, e := strconv.Atoi(os.Getenv("ORDER_COUNT"))
		if e != nil || n < 10000 || n > 2000000 {
			return fmt.Errorf("invalid order bound")
		}
		return export(ctx, pg, dir, run, caseID, n/5)
	case "prepare":
		return prepared(ctx, dir, caseID)
	case "race":
		file, e := os.Open(filepath.Join(dir, "orders.csv"))
		if e != nil {
			return e
		}
		defer file.Close()
		expected := map[int64]int64{}
		reader := csv.NewReader(file)
		var last int64
		for len(expected) < 10000 {
			fields, e := reader.Read()
			if e == io.EOF {
				break
			}
			if e != nil {
				return e
			}
			id, e := strconv.ParseInt(fields[0], 10, 64)
			if e != nil {
				return e
			}
			revision, e := strconv.ParseInt(fields[3], 10, 64)
			if e != nil {
				return e
			}
			expected[id] = revision
			last = id
		}
		rows, e := pg.QueryContext(ctx, "SELECT id,revision,cancelled FROM orders WHERE id<=$1 ORDER BY id", last)
		if e != nil {
			return e
		}
		defer rows.Close()
		var advanced, cancelled int64
		for rows.Next() {
			var id, revision int64
			var deleted bool
			if e = rows.Scan(&id, &revision, &deleted); e != nil {
				return e
			}
			if revision > expected[id] {
				advanced++
				if deleted {
					cancelled++
				}
			}
		}
		if e = rows.Err(); e != nil {
			return e
		}
		emit(map[string]any{"event": "race_source", "advanced_rows": advanced, "cancelled_advanced_rows": cancelled})
		if advanced == 0 || cancelled == 0 {
			return fmt.Errorf("live advancement and cancellation versus exported snapshot not demonstrated")
		}
		return nil
	case "status":
		return transactions(ctx, doris, dir, false)
	case "commit":
		return transactions(ctx, doris, dir, true)
	default:
		return fmt.Errorf("unknown mode %s", mode)
	}
}
func main() {
	err := run()
	var r syscall.Rusage
	if e := syscall.Getrusage(syscall.RUSAGE_SELF, &r); e == nil {
		emit(map[string]any{"event": "resources", "peak_rss_kib": r.Maxrss, "user_cpu_s": float64(r.Utime.Sec) + float64(r.Utime.Usec)/1e6, "system_cpu_s": float64(r.Stime.Sec) + float64(r.Stime.Usec)/1e6})
	}
	if err != nil {
		emit(map[string]any{"event": "fatal", "error": err.Error()})
		os.Exit(1)
	}
}
