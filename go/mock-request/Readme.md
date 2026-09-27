# Mock Request

A basic example of mocking HTTP requests in Go. The `request` package fetches a URL, but it depends on a one-method `Doer` interface (`Do(*http.Request) (*http.Response, error)`) rather than on `*http.Client` directly. `*http.Client` already satisfies it, so production code (`cmd/httpmock.go`) passes a real client while tests substitute something else.

The tests in `request/request_test.go` show two approaches:

1. **Generated mock ([gomock](https://github.com/uber-go/mock))**: `mockgen` generates `request/mocks/mock_doer.go` from the `Doer` interface (see the `//go:generate` directive in `request/request.go`). Tests set expectations on the request (method, URL, headers) and return canned responses or errors. No network or server is involved.
2. **In-process server (`net/http/httptest`)**: a real `*http.Client` talks to a local `httptest.Server`, which exercises the whole HTTP stack (status codes, headers, bodies) without reaching the internet.

The tests also use a third option: a hand-rolled fake (`fakeDoer`, a function that satisfies `Doer`). It covers the edge cases: the User-Agent header, a body read error, an invalid URL, and context cancellation. An httptest-based test covers context timeouts.

Use a mock when you want to assert exactly how the dependency was called or to simulate failures such as transport errors. Use `httptest` when you want realistic HTTP behavior. For the [mockery](https://github.com/vektra/mockery) and testify approach, see `../mockery-of-language`.

**Note:** [golang/mock](https://github.com/golang/mock) is archived. This example uses its maintained fork, `go.uber.org/mock`.

### Benchmarks

`BenchmarkFetch` in `request/request_bench_test.go` runs the same `Fetcher.Fetch` call against each test double. Sample results (Apple M4 Pro, Go 1.26):

| Backend    | ns/op  | B/op | allocs/op |
|------------|-------:|-----:|----------:|
| fake Doer  |    370 | 1616 |        10 |
| gomock     |    947 | 1808 |        16 |
| httptest   | 29,851 | 6350 |        68 |

The hand-rolled fake has the lowest overhead. gomock costs roughly 2-3x more because it matches expectations and records calls through reflection, but that is still well under a microsecond. `httptest` is about 30-80x slower because every call goes through a real TCP loopback connection and the full `net/http` client and server stack. That cost buys realism, so it is usually worth it for a handful of integration-style tests. For large table-driven suites, prefer a fake or a mock.

### Running

```sh
# run locally
go generate ./...     # regenerate the mocks
go test -v -race ./...
go run ./cmd -url https://example.com

# benchmarks comparing the test doubles
go test -run='^$' -bench=. -benchmem ./...

# or with docker: regenerate the mocks and run the tests, then run the CLI
docker compose up --build mockgenerator
docker compose run --rm --build httpmock -url https://go.dev

# delete the containers and their images
docker compose down --rmi all --volumes
```
