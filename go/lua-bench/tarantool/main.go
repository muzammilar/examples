// lua-bench/tarantool runs the workload of go/rueidis-lua-bench against Tarantool: put/get
// (plain REPLACE and SELECT requests, the SET/GET equivalent) and three Lua stored functions
// with the semantics of its add.lua, update.lua and delete.lua (a versioned "hash"), called
// with IPROTO CALL through the official go-tarantool connector. Same flags (-addr -n -c -keys)
// and the same output (ops/s, p50, p99 per command).
package main

import (
	"context"
	_ "embed"
	"flag"
	"fmt"
	"log"
	"time"

	"github.com/tarantool/go-tarantool/v3"
)

var (
	//go:embed lua/add.lua
	addSrc string
	//go:embed lua/update.lua
	updateSrc string
	//go:embed lua/delete.lua
	deleteSrc string
)

// schema creates and empties the two memtx spaces:
//
//	bench_s {key, value}            put/get: plain REPLACE / SELECT requests, no Lua
//	bench_h {key, version, fields}  inventory items for add/update/delete
const schema = `
local s = box.schema.space.create('bench_s', { if_not_exists = true,
    format = { { 'key', 'string' }, { 'value', 'string' } } })
s:create_index('pk', { parts = { 'key' }, if_not_exists = true })
local h = box.schema.space.create('bench_h', { if_not_exists = true,
    format = { { 'key', 'string' }, { 'version', 'unsigned' }, { 'fields', 'map' } } })
h:create_index('pk', { parts = { 'key' }, if_not_exists = true })
s:truncate()
h:truncate()
return box.info.version`

func main() {
	addr := flag.String("addr", "127.0.0.1:3301", "Tarantool address (host:port); for a replicaset, the leader")
	n := flag.Int("n", 200000, "operations per command")
	workers := flag.Int("c", 50, "concurrent workers")
	keys := flag.Int("keys", 100000, "number of distinct keys")
	user := flag.String("user", "guest", "user (needs eval and space creation, e.g. role super)")
	password := flag.String("password", "", "password")
	router := flag.Bool("router", false, "-addr is a vshard router that defines bench_reset, bench_put, "+
		"bench_get, bench_add, bench_update and bench_delete (no schema or function setup from here)")
	flag.Parse()

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	conn, err := tarantool.Connect(ctx, tarantool.NetDialer{Address: *addr, User: *user, Password: *password},
		tarantool.Opts{Timeout: 30 * time.Second})
	if err != nil {
		log.Fatalf("connect %s: %v", *addr, err)
	}
	defer conn.Close()

	// A vshard router has the bench_* functions in its app file; each forwards the call to the
	// storage that owns the key's bucket. bench_reset empties the spaces on every storage.
	if *router {
		res, err := conn.Do(tarantool.NewCallRequest("bench_reset")).Get()
		if err != nil {
			log.Fatalf("bench_reset: %v", err)
		}
		fmt.Printf("%s: Tarantool %v (vshard router)\n", *addr, res[0])
		bench(conn, *n, *workers, *keys, true)
		return
	}

	// EVAL the schema, then each lua/*.lua: they define the functions as Lua globals, which
	// live in the instance's memory until it restarts
	res, err := conn.Do(tarantool.NewEvalRequest(schema)).Get()
	if err != nil {
		log.Fatalf("schema: %v", err)
	}
	for name, src := range map[string]string{"add.lua": addSrc, "update.lua": updateSrc, "delete.lua": deleteSrc} {
		if _, err := conn.Do(tarantool.NewEvalRequest(src)).Get(); err != nil {
			log.Fatalf("%s: %v", name, err)
		}
	}
	fmt.Printf("%s: Tarantool %v\n", *addr, res[0])
	bench(conn, *n, *workers, *keys, false)
}
