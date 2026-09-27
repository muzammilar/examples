# Mock Request

A basic example of mocking HTTP requests in Go. The `request` package fetches a URL, but it depends on a one-method `Doer` interface (`Do(*http.Request) (*http.Response, error)`) rather than on `*http.Client` directly. `*http.Client` already satisfies it, so production code (`cmd/httpmock.go`) passes a real client while tests substitute something else.

The tests in `request/request_test.go` show two approaches:

1. **Generated mock ([gomock](https://github.com/uber-go/mock))**: `mockgen` generates `request/mocks/mock_doer.go` from the `Doer` interface (see the `//go:generate` directive in `request/request.go`). Tests set expectations on the request (method, URL, headers) and return canned responses or errors. No network or server is involved.
2. **In-process server (`net/http/httptest`)**: a real `*http.Client` talks to a local `httptest.Server`, which exercises the whole HTTP stack (status codes, headers, bodies) without reaching the internet.

The tests also use a third option: a hand-rolled fake (`fakeDoer`, a function that satisfies `Doer`). It covers the edge cases: the User-Agent header, a body read error, an invalid URL, and context cancellation. An httptest-based test covers context timeouts.

Use a mock when you want to assert exactly how the dependency was called or to simulate failures such as transport errors. Use `httptest` when you want realistic HTTP behavior. For the [mockery](https://github.com/vektra/mockery) and testify approach, see `../mockery-of-language`.

**Note:** [golang/mock](https://github.com/golang/mock) is archived. This example uses its maintained fork, `go.uber.org/mock`.

### Benchmarks

`BenchmarkFetch` in `request/request_bench_test.go` runs the same `Fetcher.Fetch` call against each test double. Sample results (Apple M4 Pro, Go 1.26, `make bench BENCH_TIME=100ms`):

| Backend           |  ns/op | B/op | allocs/op |
|-------------------|-------:|-----:|----------:|
| fake Doer         |    483 | 1616 |        10 |
| instrumented-fake |    797 | 1723 |        14 |
| gomock            |  1,101 | 1808 |        16 |
| httptest          | 41,523 | 6371 |        68 |

The `instrumented-fake` case wraps the fake in the metrics decorator (see [Monitoring](#monitoring)). It adds roughly 300 ns and 4 allocations per call over the plain fake.

The hand-rolled fake has the lowest overhead. gomock costs roughly 2x more because it matches expectations and records calls through reflection, but that is still well under a microsecond. `httptest` is about 40-85x slower because every call goes through a real TCP loopback connection and the full `net/http` client and server stack. That cost buys realism, so it is usually worth it for a handful of integration-style tests. For large table-driven suites, prefer a fake or a mock.

### Running

```sh
# run locally
go generate ./...     # regenerate the mocks
go test -v -race ./...
go run ./cmd -url https://example.com
go run ./cmd -urls https://example.com,https://go.dev   # several URLs, once each
go run ./cmd -urls https://example.com -interval 5s -metrics-addr :8080   # probe until Ctrl-C

# benchmarks comparing the test doubles
go test -run='^$' -bench=. -benchmem ./...

# or with docker: regenerate the mocks and run the tests, then run the CLI once
make docker-mockgen    # docker compose --profile tools run --rm --build mockgenerator
docker compose run --rm --build httpmock -url https://go.dev

# start the prober with Prometheus and Grafana (see Monitoring), then clean up
make docker-up     # Prometheus http://localhost:19116, Grafana http://localhost:13016/d/mock-request
make docker-down
```

### CLI flags

`cmd/httpmock.go` (`go run ./cmd`, or the `httpmock` image) accepts these flags:

| Flag | Default | Meaning |
|---|---|---|
| `-url` | `https://example.com` | a single URL to fetch; ignored when `-urls` is set |
| `-urls` | (none) | comma-separated URLs to fetch; can be repeated |
| `-timeout` | `10s` | timeout for each request |
| `-interval` | `0` | `0` fetches each URL once and exits; a positive duration probes every interval until SIGINT/SIGTERM |
| `-metrics-addr` | (empty, disabled) | serve Prometheus metrics on this address at `/metrics`, e.g. `:8080` |

In one-shot mode (`-interval 0`) the CLI fetches every URL, prints `URL -> status (N bytes)` or logs the error, then exits 1 if any fetch failed. A non-2xx status counts as a failure. In probe mode, failures are logged and probing continues.

### Docker and the `tools` profile

The compose project is `mockreq`. The `mockgenerator` service is in the `tools` profile, so `docker compose up` (and `make docker-up`) does not start it and does not rewrite files in the working tree. `make docker-mockgen` runs it (`docker compose --profile tools run --rm --build mockgenerator`). It mounts the example directory, runs `go generate ./...` to write the mocks back to the host, runs `go test -v ./...`, and exits.

## Monitoring

The CLI can also run as a long-lived prober: `-interval D` fetches every URL in `-urls` (comma-separated, repeatable) every `D` until SIGINT/SIGTERM, and `-metrics-addr` serves Prometheus metrics on `/metrics`. With `-interval 0` (the default) it fetches each URL once and exits non-zero if any fetch failed.

The metrics come from `request.InstrumentedDoer` in `request/metrics.go`. It is a decorator: a `Doer` that wraps another `Doer` and records each call. `Fetcher` still sees only a `Doer`, so the instrumentation needs no change to the fetching code:

```go
doer := request.NewInstrumentedDoer(&http.Client{}, request.NewMetrics(registry))
f := request.NewFetcher(doer)
```

Because the decorator depends on the interface, it is tested the same way as `Fetcher`. `request/metrics_test.go` wraps the generated gomock mock (and the hand-rolled fake), then checks the recorded series with `prometheus/testutil` against a private registry. A test-only clock hook (`export_test.go`) makes durations and timestamps deterministic.

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `httpmock_requests_total` | counter | `url`, `code` | requests that got a response, by status code |
| `httpmock_request_errors_total` | counter | `url` | requests with no response (transport errors, timeouts) |
| `httpmock_request_duration_seconds` | histogram | `url` | time until the response headers arrived, or the request failed |
| `httpmock_response_size_bytes` | histogram | `url` | body bytes read before the body was closed |
| `httpmock_last_success_timestamp_seconds` | gauge | `url` | Unix time of the last 2xx response |

The `url` label is the request URL without its query string. A non-2xx response counts in `httpmock_requests_total` with its code, not as an error. `Fetcher` reports non-2xx as an error, but the transport succeeded.

`make docker-up` starts the stack detached and waits (up to 240s) until Prometheus is ready and Grafana has provisioned its datasource and dashboard (compose healthchecks):

- `target`: a small local service (`cmd/target`, built from the same Dockerfile) with `/ok` (200), `/slow` (0-1.5s, so some requests exceed the prober's 1s timeout), `/flaky` (200/429/503), `/missing` (404) and `/error` (500). The demo therefore does not depend on the public internet.
- `httpmock`: the prober, fetching the target endpoints and https://example.com every 2s, with metrics on `:8080` inside the compose network only.
- `prometheus`: `prom/prometheus:v3.15.0`, scraping every 5s (`prometheus/prometheus.yml`), at http://localhost:19116.
- `grafana`: `grafana/grafana:13.2.2` with anonymous admin access, at http://localhost:13016. The `Prometheus` datasource (uid `prometheus`) and the **Mock Request Prober** dashboard (http://localhost:13016/d/mock-request) are provisioned from `grafana/provisioning/`.

The dashboard refreshes every 5s over the last 15 minutes. It shows the request rate, failure ratio, and 4xx and 5xx rates; the request rate by status code; non-2xx responses by URL; the transport error rate by URL; time since the last success per URL; latency p50, p95 and p99 by URL; and the average response size and bytes read per second by URL. `/missing` and `/error` never succeed, so they have no time-since-last-success series.

`make docker-down` removes the project's containers, networks, volumes and orphans, plus the three images the compose file builds (`xmpl/mockrequest`, `xmpl/mockrequest-target`, `xmpl/mockrequest-mockgen`). It keeps the pulled Prometheus and Grafana images.

## Make Targets

Run `make help` to list all targets. Unit tests and benchmarks are kept separate (plain `go test` skips `Benchmark*` functions, and `-run='^$'` skips the tests when benchmarking):

```sh
make test        # unit tests only
make test-race   # unit tests with the race detector
make bench       # benchmarks only; filter with BENCH=<regex>, tune with BENCH_TIME=2s BENCH_COUNT=5
make bench-cpu   # benchmarks at GOMAXPROCS 1, 4 and 8
make generate    # regenerate the gomock mocks
make test-all    # vet + race tests + benchmarks

make docker-up       # prober + target + Prometheus + Grafana, detached; waits until healthy, prints the URLs
make docker-logs     # follow the prober output
make docker-mockgen  # regenerate mocks and run the tests in Docker, then exit
make docker-down     # remove containers, networks, volumes and the built images
```

## Versions

- Go 1.26 (`go 1.26.0` in `go.mod`; `golang:1.26-alpine` build image, `alpine:3.24` runtime images)
- `go.uber.org/mock` and `mockgen` v0.6.0 (the `//go:generate` directive and the Dockerfile's `MOCKGEN_VERSION` must match `go.mod`; the `mockgen` stage fails the build if they differ)
- `github.com/prometheus/client_golang` v1.24.1 (indirect: `client_model` v0.6.3, `common` v0.71.0, `procfs` v0.22.0, `golang.org/x/sys` v0.48.0, `google.golang.org/protobuf` v1.36.12)
- `prom/prometheus:v3.15.0` and `grafana/grafana:13.2.2`
