// rueidis-lua-bench runs SET, GET and three Lua scripts (add, update and delete
// a versioned hash) through rueidis and prints ops/s, p50 and p99 for each.
// It works against a single node, a primary or a cluster: rueidis finds out on
// connect whether -addr is a cluster node and, if it is, sends each command to
// the primary that owns its key.
package main

import (
	"context"
	_ "embed"
	"flag"
	"fmt"
	"log"

	"github.com/redis/rueidis"
)

var (
	//go:embed lua/add.lua
	addSrc string
	//go:embed lua/update.lua
	updateSrc string
	//go:embed lua/delete.lua
	deleteSrc string

	addScript    = rueidis.NewLuaScript(addSrc)
	updateScript = rueidis.NewLuaScript(updateSrc)
	deleteScript = rueidis.NewLuaScript(deleteSrc)
)

func main() {
	addr := flag.String("addr", "127.0.0.1:6379", "server address (host:port); any node of a cluster")
	n := flag.Int("n", 200000, "operations per command")
	workers := flag.Int("c", 50, "concurrent workers")
	keys := flag.Int("keys", 100000, "number of distinct keys")
	flag.Parse()

	c, err := rueidis.NewClient(rueidis.ClientOption{InitAddress: []string{*addr}, DisableCache: true})
	if err != nil {
		log.Fatalf("connect %s: %v", *addr, err)
	}
	defer c.Close()

	fmt.Printf("%s: %s mode, %d nodes\n", *addr, c.Mode(), len(c.Nodes()))
	bench(context.Background(), c, *n, *workers, *keys)
}
