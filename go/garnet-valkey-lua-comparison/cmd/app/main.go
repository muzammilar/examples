package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"sync"
	"time"

	"github.com/muzammilar/examples-go/garnet-valkey-lua-comparison/pkg/redisops"
	"golang.org/x/sync/errgroup"
)

func main() {
	ctx := context.Background()

	// Get addresses from environment or use defaults
	valkeyAddr := os.Getenv("VALKEY_ADDR")
	if valkeyAddr == "" {
		valkeyAddr = "localhost:6379"
	}

	garnetAddr := os.Getenv("GARNET_ADDR")
	if garnetAddr == "" {
		garnetAddr = "localhost:6380"
	}

	// Connect to Valkey
	log.Printf("Connecting to Valkey at %s", valkeyAddr)
	valkeyClient := redisops.NewClient(valkeyAddr)
	defer valkeyClient.Close()

	// Connect to Garnet
	log.Printf("Connecting to Garnet at %s", garnetAddr)
	garnetClient := redisops.NewClient(garnetAddr)
	defer garnetClient.Close()

	// Test connections
	log.Println("\n=== Testing Connections ===")
	if err := valkeyClient.Ping(ctx); err != nil {
		log.Fatalf("Failed to ping Valkey: %v", err)
	}
	log.Println("✓ Valkey connection successful")

	if err := garnetClient.Ping(ctx); err != nil {
		log.Fatalf("Failed to ping Garnet: %v", err)
	}
	log.Println("✓ Garnet connection successful")

	// Dual write operations (parallel with errgroup)
	log.Println("\n=== Dual Write Operations (Parallel) ===")
	key := "test:comparison:key"
	value := "Hello from dual write!"

	var valkeyWriteDuration, garnetWriteDuration time.Duration
	var mu sync.Mutex

	g, gctx := errgroup.WithContext(ctx)

	// Write to Valkey in goroutine
	g.Go(func() error {
		start := time.Now()
		err := valkeyClient.Set(gctx, key, value, 10*time.Minute)
		mu.Lock()
		valkeyWriteDuration = time.Since(start)
		mu.Unlock()
		if err != nil {
			return fmt.Errorf("valkey write failed: %w", err)
		}
		log.Printf("✓ Valkey: Set key '%s' (took %v)", key, valkeyWriteDuration)
		return nil
	})

	// Write to Garnet in goroutine
	g.Go(func() error {
		start := time.Now()
		err := garnetClient.Set(gctx, key, value, 10*time.Minute)
		mu.Lock()
		garnetWriteDuration = time.Since(start)
		mu.Unlock()
		if err != nil {
			return fmt.Errorf("garnet write failed: %w", err)
		}
		log.Printf("✓ Garnet: Set key '%s' (took %v)", key, garnetWriteDuration)
		return nil
	})

	startWrite := time.Now()
	if err := g.Wait(); err != nil {
		log.Fatalf("Dual write failed: %v", err)
	}
	totalWriteDuration := time.Since(startWrite)
	log.Printf("✓ Parallel write completed in %v", totalWriteDuration)

	// Dual read operations (parallel with errgroup)
	log.Println("\n=== Dual Read Operations (Parallel) ===")

	var valkeyReadDuration, garnetReadDuration time.Duration
	var valkeyValue, garnetValue string

	g2, gctx2 := errgroup.WithContext(ctx)

	// Read from Valkey in goroutine
	g2.Go(func() error {
		start := time.Now()
		val, err := valkeyClient.Get(gctx2, key)
		mu.Lock()
		valkeyValue = val
		valkeyReadDuration = time.Since(start)
		mu.Unlock()
		if err != nil {
			return fmt.Errorf("valkey read failed: %w", err)
		}
		log.Printf("✓ Valkey: Retrieved '%s' (took %v)", valkeyValue, valkeyReadDuration)
		return nil
	})

	// Read from Garnet in goroutine
	g2.Go(func() error {
		start := time.Now()
		val, err := garnetClient.Get(gctx2, key)
		mu.Lock()
		garnetValue = val
		garnetReadDuration = time.Since(start)
		mu.Unlock()
		if err != nil {
			return fmt.Errorf("garnet read failed: %w", err)
		}
		log.Printf("✓ Garnet: Retrieved '%s' (took %v)", garnetValue, garnetReadDuration)
		return nil
	})

	startRead := time.Now()
	if err := g2.Wait(); err != nil {
		log.Fatalf("Dual read failed: %v", err)
	}
	totalReadDuration := time.Since(startRead)
	log.Printf("✓ Parallel read completed in %v", totalReadDuration)

	// Compare values
	if valkeyValue == garnetValue {
		log.Printf("✓ Values match: '%s'", valkeyValue)
	} else {
		log.Printf("⚠ Values differ! Valkey: '%s', Garnet: '%s'", valkeyValue, garnetValue)
	}

	// Dual Lua script execution
	log.Println("\n=== Dual Lua Script Execution ===")
	luaScript := `return redis.call('GET', KEYS[1])`

	// Execute on Valkey
	startValkey := time.Now()
	valkeyResult, err := valkeyClient.EvalLua(ctx, luaScript, []string{key})
	if err != nil {
		log.Printf("⚠ Valkey Lua script failed: %v", err)
	} else {
		valkeyLuaDuration := time.Since(startValkey)
		log.Printf("✓ Valkey Lua result: %v (took %v)", valkeyResult, valkeyLuaDuration)
	}

	// Execute on Garnet
	startGarnet := time.Now()
	garnetResult, err := garnetClient.EvalLua(ctx, luaScript, []string{key})
	if err != nil {
		log.Printf("⚠ Garnet Lua script failed: %v", err)
	} else {
		garnetLuaDuration := time.Since(startGarnet)
		log.Printf("✓ Garnet Lua result: %v (took %v)", garnetResult, garnetLuaDuration)
	}

	// Lua script from file (if provided)
	luaFilePath := os.Getenv("LUA_SCRIPT_PATH")
	if luaFilePath != "" {
		log.Printf("\n=== Lua Script from File: %s ===", luaFilePath)

		// Execute on Valkey
		valkeyFileResult, err := valkeyClient.EvalLuaFromFile(ctx, luaFilePath, []string{key})
		if err != nil {
			log.Printf("⚠ Valkey: Failed to execute Lua script from file: %v", err)
		} else {
			log.Printf("✓ Valkey result: %v", valkeyFileResult)
		}

		// Execute on Garnet
		garnetFileResult, err := garnetClient.EvalLuaFromFile(ctx, luaFilePath, []string{key})
		if err != nil {
			log.Printf("⚠ Garnet: Failed to execute Lua script from file: %v", err)
		} else {
			log.Printf("✓ Garnet result: %v", garnetFileResult)
		}
	}

	// Cleanup from both
	log.Println("\n=== Cleanup ===")
	if err := valkeyClient.Del(ctx, key); err != nil {
		log.Printf("⚠ Failed to delete key from Valkey: %v", err)
	} else {
		log.Printf("✓ Valkey: Deleted key '%s'", key)
	}

	if err := garnetClient.Del(ctx, key); err != nil {
		log.Printf("⚠ Failed to delete key from Garnet: %v", err)
	} else {
		log.Printf("✓ Garnet: Deleted key '%s'", key)
	}

	// Summary
	fmt.Println("\n=== Performance Summary ===")
	fmt.Printf("Individual Write - Valkey: %v, Garnet: %v\n", valkeyWriteDuration, garnetWriteDuration)
	fmt.Printf("Parallel Write   - Total: %v (speedup: %.2fx)\n", totalWriteDuration, float64(valkeyWriteDuration+garnetWriteDuration)/float64(totalWriteDuration))
	fmt.Printf("\nIndividual Read  - Valkey: %v, Garnet: %v\n", valkeyReadDuration, garnetReadDuration)
	fmt.Printf("Parallel Read    - Total: %v (speedup: %.2fx)\n", totalReadDuration, float64(valkeyReadDuration+garnetReadDuration)/float64(totalReadDuration))
	fmt.Println("\n✓ All dual operations completed successfully!")
}
