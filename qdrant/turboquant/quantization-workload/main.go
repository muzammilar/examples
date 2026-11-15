package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"time"
)

var client = &http.Client{Timeout: 120 * time.Second, Transport: &http.Transport{DialContext: (&net.Dialer{Timeout: 3 * time.Second}).DialContext, MaxIdleConnsPerHost: 8}}

const results = "/results"

var modes = []string{"float-ram", "float-disk", "scalar8", "turbo4"}

func check(err error) {
	if err != nil {
		panic(err)
	}
}
func env(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
func number(key string, fallback, minimum, maximum int) int {
	v, err := strconv.Atoi(env(key, strconv.Itoa(fallback)))
	check(err)
	if v < minimum || v > maximum {
		panic("invalid " + key)
	}
	return v
}
func count() int     { return number("POINTS", 100000, 10000, 1000000) }
func dimension() int { return number("DIMENSIONS", 384, 64, 1536) }
func save(name string, value any) {
	body, err := json.MarshalIndent(value, "", "  ")
	check(err)
	path := filepath.Join(results, name+".json")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0644)
	check(err)
	_, err = file.Write(append(body, '\n'))
	check(err)
	check(file.Sync())
	check(file.Close())
	fmt.Println("Saved", path)
}
func record(file *os.File, value any) { check(json.NewEncoder(file).Encode(value)); check(file.Sync()) }
func raw(host, method, path string, input any) ([]byte, error) {
	var body []byte
	var err error
	if input != nil {
		body, err = json.Marshal(input)
		if err != nil {
			return nil, err
		}
	}
	req, err := http.NewRequest(method, "http://"+host+":6333"+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	response, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 64<<20))
	if err != nil {
		return nil, err
	}
	if response.StatusCode/100 != 2 {
		return nil, fmt.Errorf("%s %s %s: HTTP %d: %.4096s", host, method, path, response.StatusCode, data)
	}
	return data, nil
}
func call(host, method, path string, input, output any) error {
	data, err := raw(host, method, path, input)
	if err != nil {
		return err
	}
	var envelope struct {
		Result json.RawMessage `json:"result"`
		Status json.RawMessage `json:"status"`
	}
	if err = json.Unmarshal(data, &envelope); err != nil {
		return err
	}
	if string(envelope.Status) != `"ok"` {
		return fmt.Errorf("Qdrant error: %.4096s", data)
	}
	if output != nil {
		return json.Unmarshal(envelope.Result, output)
	}
	return nil
}
func main() {
	if len(os.Args) < 2 {
		panic("workload setup|seed|verify|benchmark|churn|watch|drop|api|health")
	}
	if os.Args[1] == "health" {
		client.Timeout = 3 * time.Second
		_, err := raw("127.0.0.1", "GET", "/readyz", nil)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	check(os.MkdirAll(results, 0755))
	switch os.Args[1] {
	case "ready":
		ready()
	case "setup":
		setup()
	case "seed":
		seed()
	case "verify":
		verify(true)
	case "verify-recovery":
		verify(false)
	case "benchmark":
		benchmark()
	case "churn":
		churn()
	case "watch":
		watch()
	case "drop":
		for _, mode := range modes {
			check(call("q0", "DELETE", "/collections/"+mode, nil, nil))
		}
		save("cleanup", map[string]any{"workload_dropped": true})
	case "api":
		if len(os.Args) != 6 {
			panic("api HOST METHOD PATH JSON")
		}
		var input any
		if os.Args[5] != "null" {
			decoder := json.NewDecoder(bytes.NewBufferString(os.Args[5]))
			decoder.UseNumber()
			check(decoder.Decode(&input))
		}
		data, err := raw(os.Args[2], os.Args[3], os.Args[4], input)
		check(err)
		fmt.Println(string(data))
	default:
		panic("unknown workload action")
	}
}
