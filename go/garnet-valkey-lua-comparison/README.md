# Garnet vs Valkey Lua Comparison

A Go project for comparing Garnet and Valkey Redis implementations with Lua script execution capabilities using testcontainers.

This was mostly written with Claude AI.

## Project Structure

```
.
├── cmd/
│   └── app/
│       └── main.go                 # Main application entry point
├── pkg/
│   └── redisops/
│       ├── client.go               # Redis client with basic operations
│       ├── lua.go                  # Lua script execution from files
│       ├── testconfig.go           # Test configuration constants
│       └── client_test.go          # Parallel tests for Valkey and Garnet
├── scripts/
│   └── example.lua                 # Example Lua script
├── config/
│   └── garnet.conf                 # Garnet configuration file
├── Dockerfile                      # Multi-stage Docker build
├── docker-compose.yml              # Services: Valkey, Garnet, App
├── Makefile                        # Build and run commands
├── .gitignore
└── go.mod
```

## Prerequisites

- Go 1.25 or higher
- Docker and Docker Compose
- Make

## Quick Start

### Run Tests

Run parallel tests for both Valkey and Garnet with testcontainers:

```bash
make test-containers
```

### Start Services

Start Valkey (port 6379), Garnet (port 6380), and the application:

```bash
make docker-up
```

### Check Results

View the application logs and performance comparison results:

```bash
make check-results
```

### Stop Services

Stop all services and remove volumes:

```bash
make docker-down
```

## Services

- **Valkey**: Port 6379
- **Garnet**: Port 6380 (Lua scripting enabled via `--lua` flag and config file)
- **App**: Connects to both Valkey and Garnet, performs parallel dual writes and reads

## Example Results

When you run `make check-results`, you'll see output like this:

```
=== Testing Connections ===
✓ Valkey connection successful
✓ Garnet connection successful

=== Dual Write Operations (Parallel) ===
✓ Valkey: Set key 'test:comparison:key' (took 249.417µs)
✓ Garnet: Set key 'test:comparison:key' (took 6.555875ms)
✓ Parallel write completed in 6.727ms

=== Dual Read Operations (Parallel) ===
✓ Valkey: Retrieved 'Hello from dual write!' (took 159.667µs)
✓ Garnet: Retrieved 'Hello from dual write!' (took 2.758791ms)
✓ Parallel read completed in 2.894ms
✓ Values match: 'Hello from dual write!'

=== Dual Lua Script Execution ===
✓ Valkey Lua result: Hello from dual write! (took 211.25µs)
✓ Garnet Lua result: Hello from dual write! (took 55.594625ms)

=== Lua Script from File: /app/scripts/example.lua ===
✓ Valkey result: Value from Lua: Hello from dual write!
✓ Garnet result: Value from Lua: Hello from dual write!

=== Cleanup ===
✓ Valkey: Deleted key 'test:comparison:key'
✓ Garnet: Deleted key 'test:comparison:key'

=== Performance Summary ===
Individual Write - Valkey: 249µs, Garnet: 6.5ms
Parallel Write   - Total: 6.7ms (speedup: 1.02x)

Individual Read  - Valkey: 159µs, Garnet: 2.7ms
Parallel Read    - Total: 2.9ms (speedup: 1.00x)

✓ All dual operations completed successfully!
```
