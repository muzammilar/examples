package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

type margin struct {
	ID, Customer, Amount, Cost, Shipping                      int64
	Region                                                    string
	Discount, Margin, OrderRevision, CustomerRevision, Second int64
}

func query(ctx context.Context, host, statement string) (*http.Response, error) {
	if host != "ch1" && host != "ch2" && host != "ch3" {
		return nil, errors.New("unexpected target endpoint")
	}
	req, err := http.NewRequestWithContext(ctx, "GET", "http://"+host+":8123/?"+url.Values{"query": {statement}}.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth("lab", "local-control-only")
	r, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if r.StatusCode != 200 {
		data, _ := io.ReadAll(io.LimitReader(r.Body, 8192))
		r.Body.Close()
		return nil, fmt.Errorf("ClickHouse HTTP %d: %s", r.StatusCode, data)
	}
	return r, nil
}

func decodeMargin(raw []json.RawMessage) (margin, error) {
	var result margin
	if len(raw) != 11 {
		return result, errors.New("expected eleven columns")
	}
	fields := []*int64{&result.ID, &result.Customer, &result.Amount, &result.Cost, &result.Shipping, nil, &result.Discount, &result.Margin, &result.OrderRevision, &result.CustomerRevision, &result.Second}
	for i, field := range fields {
		text := strings.TrimSpace(string(raw[i]))
		if text == "null" {
			return result, errors.New("null in required field")
		}
		if field == nil {
			if err := json.Unmarshal(raw[i], &result.Region); err != nil {
				return result, err
			}
			continue
		}
		if strings.HasPrefix(text, "\"") {
			if err := json.Unmarshal(raw[i], &text); err != nil {
				return result, err
			}
		}
		value, err := strconv.ParseInt(text, 10, 64)
		if err != nil {
			return result, err
		}
		*field = value
	}
	return result, nil
}

func scanMargin(rows *sql.Rows) (margin, error) {
	var r margin
	err := rows.Scan(&r.ID, &r.Customer, &r.Amount, &r.Cost, &r.Shipping, &r.Region, &r.Discount, &r.Margin, &r.OrderRevision, &r.CustomerRevision, &r.Second)
	return r, err
}

func sourceQuery(ctx context.Context, db *sql.DB) (string, error) {
	var count int
	if err := db.QueryRowContext(ctx, "SELECT count(*) FROM information_schema.columns WHERE table_schema='orders' AND table_name='orders' AND column_name='shipping_cents'").Scan(&count); err != nil {
		return "", err
	}
	shipping := "0"
	if count == 1 {
		shipping = "o.shipping_cents"
	}
	return "SELECT o.id,o.customer_id,o.amount_cents,o.cost_cents," + shipping + ",c.region,c.discount_bps,o.amount_cents-o.cost_cents-" + shipping + "-(o.amount_cents*c.discount_bps DIV 10000),o.revision,c.revision,o.event_second FROM orders o JOIN customers c ON c.id=o.customer_id WHERE o.cancelled=0 ORDER BY o.id", nil
}

func verify(ctx context.Context, db *sql.DB) error {
	statement, err := sourceQuery(ctx, db)
	if err != nil {
		return err
	}
	for _, host := range []string{"ch1", "ch2", "ch3"} {
		began := time.Now()
		source, err := db.QueryContext(ctx, statement)
		if err != nil {
			return err
		}
		r, err := query(ctx, host, "SELECT * FROM pipeline.current_margins ORDER BY order_id FORMAT JSONCompactEachRow")
		if err != nil {
			source.Close()
			return err
		}
		decoder := json.NewDecoder(bufio.NewReader(r.Body))
		hash := sha256.New()
		count := 0
		for source.Next() {
			want, e := scanMargin(source)
			if e != nil {
				err = e
				break
			}
			var raw []json.RawMessage
			if err = decoder.Decode(&raw); err != nil {
				break
			}
			got, e := decodeMargin(raw)
			if e != nil {
				err = e
				break
			}
			if got != want {
				err = fmt.Errorf("%s wrong row: got %+v want %+v", host, got, want)
				break
			}
			fmt.Fprintln(hash, got)
			count++
		}
		if err == nil {
			err = source.Err()
		}
		if err == nil {
			var extra []json.RawMessage
			e := decoder.Decode(&extra)
			if !errors.Is(e, io.EOF) {
				err = fmt.Errorf("%s extra row or invalid tail: %v %s", host, e, extra)
			}
		}
		r.Body.Close()
		source.Close()
		if err != nil {
			return err
		}
		emit(map[string]any{"event": "verified", "host": host, "rows": count, "body_sha256": hex.EncodeToString(hash.Sum(nil)), "duration_ms": float64(time.Since(began)) / float64(time.Millisecond)})
	}
	return nil
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
		got, err := decodeMargin(raw)
		if err != nil {
			return count, err
		}
		id := got.ID
		if id <= previous || id < base || id > base+63 {
			return count, fmt.Errorf("unordered, duplicate or out-of-range row: %d after %d", id, previous)
		}
		customer := (id-1)%customers + 1
		amount, cost, discount := 1000+id%10000, 600+id%500, (customer%6)*100
		want := margin{id, customer, amount, cost, 0, fmt.Sprintf("r%d", customer%10), discount, amount - cost - amount*discount/10000, 1, 1, 1700000000 + id%86400}
		if got != want {
			return count, fmt.Errorf("wrong historical margin: got %+v want %+v", got, want)
		}
		previous = id
		count++
	}
}

func reader(ctx context.Context, n, customers int64) error {
	root, err := casePath()
	if err != nil {
		return err
	}
	deadline := time.Now().Add(600 * time.Second)
	var pending *acknowledgedInsert
	var lastVisible int64
	base := (n-565)/customers*customers + 501
	for time.Now().Before(deadline) && ctx.Err() == nil && !exists(root+"/stop-reader") {
		began := time.Now()
		r, e := query(ctx, "ch1", fmt.Sprintf("SELECT * FROM pipeline.current_margins WHERE order_id BETWEEN %d AND %d ORDER BY order_id FORMAT JSONCompactEachRow", base, base+63))
		count := int64(0)
		if e == nil {
			count, e = readImmutable(r.Body, base, customers)
			r.Body.Close()
		}
		result := "ok"
		if e != nil || count != 64 {
			result = "error"
		}
		emit(map[string]any{"event": "read", "result": result, "rows": count, "phase": phase(), "duration_ms": float64(time.Since(began)) / float64(time.Millisecond), "utc_ns": time.Now().UnixNano(), "error": fmt.Sprint(e)})
		if e != nil {
			return e
		}
		if count != 64 {
			return errors.New("historical rows missing after baseline verification")
		}
		if pending == nil {
			pending, err = latestInsert(root)
			if err != nil {
				return err
			}
			if pending != nil && pending.ID <= lastVisible {
				pending = nil
			}
		}
		if pending != nil {
			visible, err := observeRecent(ctx, pending)
			if err != nil {
				return err
			}
			if visible {
				lastVisible = pending.ID
				pending = nil
			}
		}
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(200 * time.Millisecond):
		}
	}
	return nil
}

func saveJSON(name string, value any) error {
	root, err := casePath()
	if err != nil {
		return err
	}
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	temporary := root + "/" + name + ".next"
	if err = os.WriteFile(temporary, append(data, '\n'), 0600); err != nil {
		return err
	}
	return os.Rename(temporary, root+"/"+name)
}
