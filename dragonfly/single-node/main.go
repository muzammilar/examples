package main

import (
	"context"
	"crypto/sha1"
	_ "embed"
	"fmt"
	"log"
	"os"
	"sync"

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
	addr := os.Getenv("DRAGONFLY_ADDR")
	if addr == "" {
		addr = "127.0.0.1:6391"
	}
	c, err := rueidis.NewClient(rueidis.ClientOption{InitAddress: []string{addr}, DisableCache: true})
	if err != nil {
		log.Fatalf("connect %s: %v", addr, err)
	}
	defer c.Close()
	ctx := context.Background()

	add := func(key string, fields ...string) int64 {
		return must(addScript.Exec(ctx, c, []string{key}, fields).AsInt64())
	}
	update := func(key, version string, fields ...string) int64 {
		return must(updateScript.Exec(ctx, c, []string{key}, append([]string{version}, fields...)).AsInt64())
	}
	del := func(version string, keys ...string) int64 {
		return must(deleteScript.Exec(ctx, c, keys, []string{version}).AsInt64())
	}

	c.Do(ctx, c.B().Del().Key("item:1", "item:2", "item:cas").Build())

	// After SCRIPT FLUSH the first EVALSHA gets NOSCRIPT and rueidis retries with EVAL,
	// which also puts the script back in the cache.
	if err := c.Do(ctx, c.B().ScriptFlush().Build()).Error(); err != nil {
		log.Fatal(err)
	}
	expect("add item:1 (after SCRIPT FLUSH)", add("item:1", "name", "widget", "qty", "1"), 1)
	sha := fmt.Sprintf("%x", sha1.Sum([]byte(addSrc)))
	cached := must(c.Do(ctx, c.B().ScriptExists().Sha1(sha).Build()).AsIntSlice())
	expect("SCRIPT EXISTS add", cached[0], 1)

	expect("add item:1 again", add("item:1", "name", "other"), 0)
	expect("update item:1 qty=5", update("item:1", "", "qty", "5"), 2)
	expect("update item:1 at stale version 1", update("item:1", "1", "qty", "6"), -1)
	expect("update item:1 at version 2", update("item:1", "2", "qty", "7"), 3)
	expect("update item:2 (missing)", update("item:2", "", "qty", "1"), 0)
	fmt.Println("item:1 =", must(c.Do(ctx, c.B().Hgetall().Key("item:1").Build()).AsStrMap()))

	expect("add item:2", add("item:2", "name", "gadget"), 1)
	expect("delete item:1 at version 1", del("1", "item:1"), 0)
	expect("delete item:1 item:2", del("", "item:1", "item:2"), 2)

	// 10 goroutines update the same item at version 1: one wins, the rest see -1.
	add("item:cas", "owner", "nobody")
	var wg sync.WaitGroup
	var mu sync.Mutex
	wins := 0
	for i := range 10 {
		wg.Go(func() {
			if update("item:cas", "1", "owner", fmt.Sprint("worker-", i)) == 2 {
				mu.Lock()
				wins++
				mu.Unlock()
			}
		})
	}
	wg.Wait()
	expect("CAS winners out of 10", int64(wins), 1)
	expect("delete item:cas at version 2", del("2", "item:cas"), 1)

	fmt.Println("ok")
}

func expect(step string, got, want int64) {
	fmt.Printf("%s: %d\n", step, got)
	if got != want {
		log.Fatalf("%s: got %d, want %d", step, got, want)
	}
}

func must[T any](v T, err error) T {
	if err != nil {
		log.Fatal(err)
	}
	return v
}
