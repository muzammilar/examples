// Minimal acknowledged-write oracle for a remote-WAL bootstrap/replay failure.
package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

func check(err error) {
	if err != nil {
		panic(err)
	}
}
func sql(statement string) [][]any {
	client := http.Client{Timeout: 15 * time.Second}
	response, err := client.PostForm("http://front:4000/v1/sql?db=public", url.Values{"sql": {statement}})
	check(err)
	defer response.Body.Close()
	body, err := io.ReadAll(response.Body)
	check(err)
	if response.StatusCode != 200 {
		panic(string(body))
	}
	var result struct {
		Code   int    `json:"code"`
		Error  string `json:"error"`
		Output []struct {
			Records *struct {
				Rows [][]any `json:"rows"`
			} `json:"records"`
		} `json:"output"`
	}
	check(json.Unmarshal(body, &result))
	if result.Code != 0 {
		panic(result.Error)
	}
	if len(result.Output) == 1 && result.Output[0].Records != nil {
		return result.Output[0].Records.Rows
	}
	return nil
}
func save(name string, value any) {
	file, err := os.OpenFile("/results/"+name+".json", os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0644)
	check(err)
	check(json.NewEncoder(file).Encode(value))
	check(file.Sync())
	check(file.Close())
}
func journal(stage string, n int) {
	file, err := os.OpenFile("/results/ack-journal.jsonl", os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0644)
	check(err)
	check(json.NewEncoder(file).Encode(map[string]any{"stage": stage, "rows": n, "at": time.Now().UTC()}))
	check(file.Sync())
	check(file.Close())
}
func body(id int) string {
	return fmt.Sprintf("%x", sha256.Sum256([]byte(fmt.Sprintf("wal-repro:%d", id))))
}
func insert(ids []int) {
	rows := make([]string, 0, len(ids))
	for _, id := range ids {
		rows = append(rows, fmt.Sprintf("(%d,'%s',%d)", id, body(id), 1700000000000+id))
	}
	if len(rows) > 0 {
		sql("INSERT INTO wal_repro VALUES " + strings.Join(rows, ","))
	}
}
func read(n int) (map[string]any, []int) {
	seen := make([]bool, n)
	wrong, duplicates := 0, 0
	for _, row := range sql("SELECT event_id,payload,CAST(ts AS BIGINT) FROM wal_repro") {
		id := int(row[0].(float64))
		if id < 0 || id >= n {
			wrong++
			continue
		}
		if seen[id] {
			duplicates++
		}
		seen[id] = true
		if row[1] != body(id) || int64(row[2].(float64)) != 1700000000000+int64(id) {
			wrong++
		}
	}
	missing := []int{}
	for id, present := range seen {
		if !present {
			missing = append(missing, id)
		}
	}
	return map[string]any{"expected_rows": n, "observed_rows": n - len(missing), "missing_rows": len(missing), "missing_ids": missing, "wrong": wrong, "duplicates": duplicates}, missing
}
func main() {
	check(os.MkdirAll("/results", 0755))
	n, err := strconv.Atoi(os.Getenv("ROWS"))
	check(err)
	if n < 1 || n > 10000 {
		panic("ROWS must be 1..10000 for the minimal reproducer")
	}
	if len(os.Args) != 2 {
		panic("expected seed, verify or repair")
	}
	switch os.Args[1] {
	case "seed":
		if _, err := os.Stat("/results/ack-journal.jsonl"); !os.IsNotExist(err) {
			panic("existing journal; preserve it and use full cleanup before a fresh fixture")
		}
		sql("CREATE TABLE wal_repro(event_id BIGINT,payload STRING,ts TIMESTAMP(3) TIME INDEX,PRIMARY KEY(event_id)) ENGINE=mito")
		ids := make([]int, n)
		for id := range ids {
			ids[id] = id
		}
		journal("attempt", n)
		insert(ids)
		journal("ack", n)
		save("seed", map[string]any{"acked_rows": n})
	case "verify":
		result, missing := read(n)
		save("readback", result)
		check(json.NewEncoder(os.Stdout).Encode(result))
		if len(missing) > 0 || result["wrong"].(int) > 0 || result["duplicates"].(int) > 0 {
			os.Exit(1)
		}
	case "repair":
		before, missing := read(n)
		if before["wrong"].(int) != 0 || before["duplicates"].(int) != 0 {
			panic("refusing repair of corrupt or duplicate data")
		}
		save("before-manual-repair", before)
		journal("manual-repair-attempt", len(missing))
		insert(missing)
		journal("manual-repair-ack", len(missing))
		save("manual-repair", map[string]any{"replayed_rows": len(missing), "source": "deterministic external ACK fixture"})
	default:
		panic("unknown action")
	}
}
