package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type row struct {
	ID, Customer, Amount                        int64
	Region                                      string
	Revision, CustomerRevision, Second, Version int64
}

func emit(v any) {
	if err := json.NewEncoder(os.Stdout).Encode(v); err != nil {
		panic(err)
	}
}
func bounded(name string, fallback, maximum int64) int64 {
	s := os.Getenv(name)
	if s == "" {
		return fallback
	}
	v, e := strconv.ParseInt(s, 10, 64)
	if e != nil || v < 1 || v > maximum {
		panic("invalid " + name)
	}
	return v
}
func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	if err := run(ctx); err != nil {
		emit(map[string]any{"event": "fatal", "error": err.Error()})
		os.Exit(1)
	}
}
func run(ctx context.Context) error {
	if len(os.Args) != 2 {
		return errors.New("one mode required")
	}
	mode := os.Args[1]
	source, err := os.ReadFile("/source.sha256")
	if err != nil {
		return err
	}
	emit(map[string]any{"event": "source", "manifest": string(source)})
	if mode == "idle" {
		<-ctx.Done()
		return nil
	}
	cfg, err := pgxpool.ParseConfig(os.Getenv("SOURCE_URL"))
	if err != nil {
		return err
	}
	cfg.MaxConns = 4
	cfg.ConnConfig.ConnectTimeout = 5 * time.Second
	cfg.ConnConfig.RuntimeParams["statement_timeout"] = "60000"
	pg, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return err
	}
	defer pg.Close()
	n, customers := bounded("ORDER_COUNT", 1000000, 100000000), bounded("CUSTOMER_COUNT", 10000, 60000)
	if n < 100000 || customers < 1000 {
		return errors.New("fixture requires at least 100000 orders and 1000 customers")
	}
	emit(map[string]any{"event": "configuration", "mode": mode, "orders": n, "customers": customers, "pool": 4, "op_timeout_s": 60, "retries": 0, "http_timeout_s": 90})
	switch mode {
	case "seed":
		return seed(ctx, pg, n, customers)
	case "writer":
		return writer(ctx, pg, n, customers)
	case "reader":
		return reader(ctx, n, customers)
	case "verify":
		return verify(ctx, pg)
	default:
		return errors.New("unknown mode")
	}
}
func seed(ctx context.Context, pg *pgxpool.Pool, n, c int64) error {
	tx, err := pg.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, "INSERT INTO customers SELECT i,'r'||(i%10)::text,1 FROM generate_series(1,$1::bigint) i", c); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, "INSERT INTO orders SELECT i,(i-1)%$2+1,1000+i%10000,1,false,1700000000+i%86400 FROM generate_series(1,$1::bigint) i", n, c); err != nil {
		return err
	}
	if err = tx.Commit(ctx); err != nil {
		return fmt.Errorf("seed commit ambiguous: %w", err)
	}
	emit(map[string]any{"event": "seed_ack", "orders": n, "customers": c})
	return nil
}
func casePath() (string, error) {
	id := os.Getenv("RUN_ID")
	if len(id) != 32 || strings.Trim(id, "0123456789abcdef") != "" {
		return "", errors.New("invalid run identity")
	}
	root := filepath.Join("/results/cases", id)
	return root, os.MkdirAll(root, 0755)
}
func appendRecord(f *os.File, v any) error {
	data, e := json.Marshal(v)
	if e != nil {
		return e
	}
	data = append(data, '\n')
	if _, e = f.Write(data); e != nil {
		return e
	}
	return f.Sync()
}
func exists(path string) bool { _, err := os.Stat(path); return err == nil }
func phase() string           { b, _ := os.ReadFile("/results/phase"); return strings.TrimSpace(string(b)) }
func writer(ctx context.Context, pg *pgxpool.Pool, n, c int64) error {
	root, err := casePath()
	if err != nil {
		return err
	}
	f, err := os.OpenFile(root+"/witness.jsonl", os.O_CREATE|os.O_EXCL|os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return err
	}
	defer f.Close()
	deadline := time.Now().Add(time.Duration(bounded("DURATION_S", 600, 900)) * time.Second)
	for seq := int64(1); time.Now().Before(deadline) && ctx.Err() == nil && !exists(root+"/stop"); seq++ {
		id := n + seq
		customer := (seq-1)%c + 1
		amount := 1000 + 3*seq
		second := 1700000000 + seq
		payload := fmt.Sprintf("%d:%d:%d:%d", id, customer, amount, second)
		sum := sha256.Sum256([]byte(payload))
		receipt := os.Getenv("RUN_ID") + ":" + strconv.FormatInt(seq, 10)
		record := map[string]any{"event": "intent", "sequence": seq, "receipt": receipt, "id": id, "customer_id": customer, "amount_cents": amount, "event_second": second, "sha256": hex.EncodeToString(sum[:]), "utc_start_ns": time.Now().UnixNano(), "phase": phase()}
		if err = appendRecord(f, record); err != nil {
			return err
		}
		began := time.Now()
		opctx, cancel := context.WithTimeout(ctx, 60*time.Second)
		tx, e := pg.Begin(opctx)
		if e == nil {
			_, e = tx.Exec(opctx, "INSERT INTO orders VALUES($1,$2,$3,1,false,$4)", id, customer, amount, second)
			if e == nil {
				_, e = tx.Exec(opctx, "UPDATE orders SET amount_cents=$2,revision=revision+1,cancelled=$3 WHERE id=$1", (seq-1)%10000+1, amount, seq%5 == 0)
			}
			if e == nil && seq%7 == 0 {
				_, e = tx.Exec(opctx, "DELETE FROM orders WHERE id=$1", 20000+(seq%10000))
			}
			if e == nil && seq%20 == 0 {
				_, e = tx.Exec(opctx, "UPDATE customers SET region=$2,revision=revision+1 WHERE id=$1", (seq/20-1)%100+1, fmt.Sprintf("m%d", seq%7))
			}
			if e == nil {
				_, e = tx.Exec(opctx, "INSERT INTO receipts(id,operation,first_id,last_id,affected) VALUES($1,'live',$2,$2,1)", receipt, id)
			}
			if e == nil {
				e = tx.Commit(opctx)
			} else {
				_ = tx.Rollback(context.Background())
			}
		}
		cancel()
		record["event"] = "ack"
		if e != nil {
			record["event"] = "ambiguous"
			record["error"] = e.Error()
		}
		record["duration_ms"] = float64(time.Since(began)) / float64(time.Millisecond)
		record["utc_end_ns"] = time.Now().UnixNano()
		if err = appendRecord(f, record); err != nil {
			return err
		}
		emit(record)
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
func query(ctx context.Context, host, sql string) (*http.Response, error) {
	if host != "ch1" && host != "ch2" && host != "ch3" {
		return nil, errors.New("unowned ClickHouse endpoint")
	}
	req, e := http.NewRequestWithContext(ctx, "GET", "http://"+host+":8123/?"+url.Values{"query": {sql}}.Encode(), nil)
	if e != nil {
		return nil, e
	}
	req.SetBasicAuth("lab", "local-control-only")
	r, e := (&http.Client{Timeout: 90 * time.Second}).Do(req)
	if e != nil {
		return nil, e
	}
	if r.StatusCode != 200 {
		b, _ := io.ReadAll(io.LimitReader(r.Body, 8192))
		r.Body.Close()
		return nil, fmt.Errorf("ClickHouse %d: %s", r.StatusCode, b)
	}
	return r, nil
}
func decodeRow(raw []json.RawMessage) (row, error) {
	var r row
	if len(raw) != 8 {
		return r, errors.New("expected eight columns")
	}
	values := []*int64{&r.ID, &r.Customer, &r.Amount, nil, &r.Revision, &r.CustomerRevision, &r.Second, &r.Version}
	for i, p := range values {
		if strings.TrimSpace(string(raw[i])) == "null" {
			return r, errors.New("null in required column")
		}
		if p == nil {
			if e := json.Unmarshal(raw[i], &r.Region); e != nil {
				return r, e
			}
			continue
		}
		s := string(raw[i])
		if strings.HasPrefix(s, "\"") {
			if e := json.Unmarshal(raw[i], &s); e != nil {
				return r, e
			}
		}
		v, e := strconv.ParseInt(s, 10, 64)
		if e != nil {
			return r, e
		}
		*p = v
	}
	return r, nil
}
func readImmutable(body io.Reader, base, customers int64) (int64, error) {
	decoder := json.NewDecoder(body)
	count, previous := int64(0), base-1
	for {
		var raw []json.RawMessage
		if err := decoder.Decode(&raw); err != nil {
			if errors.Is(err, io.EOF) {
				return count, nil
			}
			return count, err
		}
		got, err := decodeRow(raw)
		if err != nil {
			return count, err
		}
		id := got.ID
		if id <= previous || id < base || id > base+63 {
			return count, fmt.Errorf("unordered, duplicate or out-of-range immutable row: %d after %d", id, previous)
		}
		customer := (id-1)%customers + 1
		want := row{id, customer, 1000 + id%10000, "r" + strconv.FormatInt(customer%10, 10), 1, 1, 1700000000 + id%86400, 1000001}
		if got != want {
			return count, fmt.Errorf("wrong immutable row: got %+v want %+v", got, want)
		}
		previous = id
		count++
	}
}

func reader(ctx context.Context, n, c int64) error {
	root, err := casePath()
	if err != nil {
		return err
	}
	deadline := time.Now().Add(time.Duration(bounded("DURATION_S", 600, 900)) * time.Second)
	for time.Now().Before(deadline) && ctx.Err() == nil && !exists(root+"/stop-reader") {
		began := time.Now()
		base := (n-565)/c*c + 501
		r, e := query(ctx, "ch1", fmt.Sprintf("SELECT * FROM pipeline.current_comparison WHERE order_id BETWEEN %d AND %d ORDER BY order_id FORMAT JSONCompactEachRow", base, base+63))
		count := int64(0)
		var failure error
		if e == nil {
			count, failure = readImmutable(r.Body, base, c)
			r.Body.Close()
		} else {
			failure = e
		}
		result := "ok"
		if failure != nil {
			result = "error"
		} else if count != 64 {
			result = "not_visible"
		}
		emit(map[string]any{"event": "read", "result": result, "rows": count, "phase": phase(), "duration_ms": float64(time.Since(began)) / float64(time.Millisecond), "utc_ns": time.Now().UnixNano(), "error": fmt.Sprint(failure)})
		if failure != nil {
			return failure
		}
		if result == "not_visible" && exists("/results/snapshot-complete") {
			return errors.New("historical rows disappeared after initial catch-up")
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(200 * time.Millisecond):
		}
	}
	return nil
}
func verify(ctx context.Context, pg *pgxpool.Pool) error {
	sql := "SELECT o.id,o.customer_id,o.amount_cents,c.region,o.revision,c.revision,o.event_second,o.revision*1000000+c.revision FROM orders o JOIN customers c ON c.id=o.customer_id WHERE NOT o.cancelled ORDER BY o.id"
	for _, host := range []string{"ch1", "ch2", "ch3"} {
		began := time.Now()
		source, e := pg.Query(ctx, sql)
		if e != nil {
			return e
		}
		r, e := query(ctx, host, "SELECT * FROM pipeline.current_comparison ORDER BY order_id FORMAT JSONCompactEachRow")
		if e != nil {
			source.Close()
			return e
		}
		d := json.NewDecoder(bufio.NewReader(r.Body))
		hash := sha256.New()
		count := 0
		for source.Next() {
			var want row
			if e = source.Scan(&want.ID, &want.Customer, &want.Amount, &want.Region, &want.Revision, &want.CustomerRevision, &want.Second, &want.Version); e != nil {
				break
			}
			var raw []json.RawMessage
			if e = d.Decode(&raw); e != nil {
				break
			}
			var got row
			got, e = decodeRow(raw)
			if e != nil {
				break
			}
			if got != want {
				e = fmt.Errorf("%s wrong row %d: got %+v want %+v", host, count, got, want)
				break
			}
			b, _ := json.Marshal(want)
			hash.Write(b)
			hash.Write([]byte{'\n'})
			count++
		}
		if e == nil {
			e = source.Err()
		}
		source.Close()
		if e == nil {
			var extra any
			if end := d.Decode(&extra); !errors.Is(end, io.EOF) {
				e = fmt.Errorf("unexpected ClickHouse tail: %v", end)
			}
		}
		r.Body.Close()
		if e != nil {
			return e
		}
		emit(map[string]any{"event": "verified", "host": host, "rows": count, "body_sha256": hex.EncodeToString(hash.Sum(nil)), "duration_ms": float64(time.Since(began)) / float64(time.Millisecond)})
	}
	return nil
}
