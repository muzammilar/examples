package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"

	_ "github.com/go-sql-driver/mysql"
)

var httpClient = &http.Client{Timeout: 120 * time.Second, Transport: &http.Transport{DialContext: (&net.Dialer{Timeout: 3 * time.Second}).DialContext, MaxIdleConnsPerHost: 4}}
var srConnections []*sql.DB
var preferredFrontend *sql.DB
var preferredBackend string
var preferredCHWriter string
var queryTimeout = 120 * time.Second

func env(key, fallback string) string {
	if text := os.Getenv(key); text != "" {
		return text
	}
	return fallback
}

func srDB() (*sql.DB, error) {
	if preferredFrontend != nil {
		return preferredFrontend, nil
	}
	if len(srConnections) == 0 {
		for _, host := range strings.Split(env("SR_FRONTENDS", "sr-fe-0,sr-fe-1,sr-fe-2"), ",") {
			db, err := sql.Open("mysql", "root@tcp("+host+":9030)/?timeout=3s&readTimeout=120s&writeTimeout=120s")
			if err != nil {
				return nil, err
			}
			db.SetMaxOpenConns(4)
			db.SetConnMaxLifetime(time.Minute)
			srConnections = append(srConnections, db)
		}
	}
	var last error
	for _, db := range srConnections {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		last = db.PingContext(ctx)
		cancel()
		if last == nil {
			preferredFrontend = db
			return db, nil
		}
	}
	return nil, fmt.Errorf("no healthy StarRocks frontend: %w", last)
}

func healthyHTTP(hosts, port, path, user, password string) (string, error) {
	for _, host := range strings.Split(hosts, ",") {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		req, err := http.NewRequestWithContext(ctx, "GET", "http://"+host+":"+port+path, nil)
		if err != nil {
			cancel()
			return "", err
		}
		req.SetBasicAuth(user, password)
		response, err := httpClient.Do(req)
		ok := err == nil && response.StatusCode == 200
		if err == nil {
			_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 8192))
			_ = response.Body.Close()
		}
		cancel()
		if ok {
			return host, nil
		}
	}
	return "", fmt.Errorf("no healthy endpoint in %s", hosts)
}

func httpRequest(method, address, user, password string, body []byte, headers map[string]string) (*http.Response, error) {
	req, err := http.NewRequest(method, address, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.SetBasicAuth(user, password)
	for name, value := range headers {
		req.Header.Set(name, value)
	}
	response, err := httpClient.Do(req)
	if err != nil {
		return nil, err
	}
	if response.StatusCode/100 != 2 {
		defer response.Body.Close()
		text, _ := io.ReadAll(io.LimitReader(response.Body, 8192))
		return nil, fmt.Errorf("%s %s: HTTP %d: %s", method, address, response.StatusCode, text)
	}
	return response, nil
}

func chRequest(host, query string, body []byte) (*http.Response, error) {
	params := url.Values{
		"query": {query}, "max_execution_time": {"120"}, "max_rows_to_read": {"5000000"},
		"max_threads": {"2"}, "max_final_threads": {"2"}, "max_block_size": {"8192"},
		"max_memory_usage": {"268435456"}, "output_format_json_quote_64bit_integers": {"0"},
		"distributed_foreground_insert": {"1"}, "insert_quorum": {strconv.Itoa(min(2, chCopies()))},
	}
	return httpRequest("POST", "http://"+host+":8123/?"+params.Encode(), "default", "local-only-clickhouse", body, nil)
}

func readRows(system, query string, visit func([]string) error) error {
	if system == "starrocks" {
		db, err := srDB()
		if err != nil {
			return err
		}
		ctx, cancel := context.WithTimeout(context.Background(), queryTimeout)
		defer cancel()
		rows, err := db.QueryContext(ctx, query)
		if err != nil {
			return err
		}
		defer rows.Close()
		columns, err := rows.Columns()
		if err != nil {
			return err
		}
		raw := make([]sql.RawBytes, len(columns))
		dest := make([]any, len(columns))
		for i := range raw {
			dest[i] = &raw[i]
		}
		for rows.Next() {
			if err := rows.Scan(dest...); err != nil {
				return err
			}
			values := make([]string, len(raw))
			for i := range raw {
				values[i] = string(raw[i])
			}
			if err := visit(values); err != nil {
				return err
			}
		}
		return rows.Err()
	}
	response, err := chRequest(env("CH_HOST", "ch00"), query+" FORMAT JSONCompactEachRow", nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	decoder := json.NewDecoder(response.Body)
	for {
		var raw []json.RawMessage
		if err := decoder.Decode(&raw); err == io.EOF {
			return nil
		} else if err != nil {
			tail, _ := io.ReadAll(io.LimitReader(io.MultiReader(decoder.Buffered(), response.Body), 4096))
			return fmt.Errorf("ClickHouse %s response: %w; body near failure: %s", env("CH_HOST", "ch00"), err, tail)
		}
		values := make([]string, len(raw))
		for i, value := range raw {
			if len(value) > 0 && value[0] == '"' {
				if err := json.Unmarshal(value, &values[i]); err != nil {
					return err
				}
			} else {
				values[i] = string(value)
			}
		}
		if err := visit(values); err != nil {
			return err
		}
	}
}

// Restrict the sorting-key range before FINAL so full-value reconciliation fits
// the same small server budget as the workload; every customer range is covered.
func readOrders(system, from string, limit int, visit func([]string) error) error {
	for first := 0; first < 10000; first += 250 {
		statement := fmt.Sprintf("SELECT customer_id,order_id,amount,status,version,fingerprint FROM %s WHERE customer_id>=%d AND customer_id<%d LIMIT %d", from, first, first+250, limit)
		if err := readRows(system, statement, visit); err != nil {
			return err
		}
	}
	outside, err := query(system, "SELECT count() FROM "+from+" WHERE customer_id<0 OR customer_id>=10000")
	if err != nil {
		return err
	}
	if len(outside) != 1 || len(outside[0]) != 1 || outside[0][0] != "0" {
		return fmt.Errorf("unexpected out-of-domain customer rows in %s", from)
	}
	return nil
}

func query(system, text string) ([][]string, error) {
	var rows [][]string
	err := readRows(system, text, func(row []string) error { rows = append(rows, row); return nil })
	return rows, err
}

func execSQL(system, text string) error {
	if system == "starrocks" {
		db, err := srDB()
		if err != nil {
			return err
		}
		ctx, cancel := context.WithTimeout(context.Background(), 120*time.Second)
		defer cancel()
		_, err = db.ExecContext(ctx, text)
		return err
	}
	response, err := chRequest(env("CH_HOST", "ch00"), text, nil)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	_, err = io.Copy(io.Discard, response.Body)
	return err
}

func streamLoad(table, label string, records any) error {
	body, err := json.Marshal(records)
	if err != nil {
		return err
	}
	if preferredBackend == "" {
		preferredBackend, err = healthyHTTP(env("SR_BACKENDS", "sr-be-0,sr-be-1,sr-be-2"), "8040", "/api/health", "root", "")
		if err != nil {
			return err
		}
	}
	headers := map[string]string{"format": "json", "strip_outer_array": "true", "strict_mode": "true", "max_filter_ratio": "0", "label": label}
	if table != "customers" {
		headers["merge_condition"] = "version"
	}
	response, err := httpRequest("PUT", "http://"+preferredBackend+":8040/api/orders_lab/"+table+"/_stream_load", "root", "", body, headers)
	if err != nil {
		preferredBackend = ""
		return err
	}
	defer response.Body.Close()
	var result struct {
		Status, Message    string
		NumberFilteredRows int64
	}
	if err := json.NewDecoder(response.Body).Decode(&result); err != nil {
		return err
	}
	if result.Status != "Success" || result.NumberFilteredRows != 0 {
		preferredBackend = ""
		return fmt.Errorf("Stream Load %s: %s: %s (filtered=%d)", label, result.Status, result.Message, result.NumberFilteredRows)
	}
	return nil
}

func insertRows(system, table, label string, records []order) error {
	if system == "starrocks" {
		return streamLoad(table, label, records)
	}
	var body bytes.Buffer
	encoder := json.NewEncoder(&body)
	for _, row := range records {
		if err := encoder.Encode(row); err != nil {
			return err
		}
	}
	if preferredCHWriter == "" {
		var err error
		preferredCHWriter, err = healthyHTTP(env("CH_WRITERS", "ch00,ch10,ch20"), "8123", "/ping", "default", "local-only-clickhouse")
		if err != nil {
			return err
		}
	}
	response, err := chRequest(preferredCHWriter, "INSERT INTO orders_lab."+table+" FORMAT JSONEachRow", body.Bytes())
	if err != nil {
		preferredCHWriter = ""
		return err
	}
	defer response.Body.Close()
	_, err = io.Copy(io.Discard, response.Body)
	return err
}
