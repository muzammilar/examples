# examples-go
Example codes in golang for fun. All examples should have associated `Dockerfile` and `docker-compose.yml` files for experimenting and development.

The `ext` directory project that are imported as git submodules.

## Summary of the Projects

`benchmark-sync-map-access`: An example on peformance benchmark reading and writing to the map data structure.

`benchmark-ip-firewall-updates`: An example on peformance benchmarks for increment integers atomically.

`chocolate-errors`: An example of passing custom errors in golang (using chocolates as references).

`clickhouse-multitable-bulk-ingest`: Bulk ingest example to clickhouse using `clickhouse-go`.

`clickhouse-struct-ingest-performance`:  Performance evaluation of `clickhouse-go`.

`dynamic-threadpool`: A worker pool that grows and shrinks between min/max bounds, with a controller that resizes it based on queue depth, graceful drain on close, Prometheus metrics and a provisioned Grafana dashboard.

`ext/geomrpc`: An example of gRPC clients and servers, including both server-side and client-side streaming and gRPC metrics collection using Prometheus (including both connection stats and RPC stats).

`file-shreder`: An example to implement a `shred` function like the [shred](https://manpages.ubuntu.com/manpages/jammy/man1/shred.1.html) command line utility with some tests.

`garnet-valkey-lua-comparison`: A comparison of Garnet and Valkey Redis implementations with Lua script execution capabilities using testcontainers.

`guage-approximator`: A basic example of implement an average gauge metric over a given time interval. The example uses a circular ring buffer to store the last n-values. The example is simliar to Prometheus' *summary* metric.

`json-parser`: A basic JSON parser example that Unmarshals a JSON stream into different structs.

`kafka-franz`: The `franz-go` equivalent of `kafka-trees`: topic creation with the `kadm` admin client, sync/async producers and a scalable consumer group on a 3-controller/3-broker KRaft cluster (Kafka 4), with `kprom` metrics, a provisioned Grafana dashboard, unit tests, benchmarks and tagged integration tests.

`kafka-trees`: A multi-topic example of sync/async producers (publishers) and a consumer group (subsribers) allowing horizontal scaling of kafka consumers. The example uses tree names as references.

`koanf-example`: An example of using koanf to read configuration from a file using environment variables and custom overrides.

`mock-request`: Mocking HTTP requests in Go with a generated [gomock](https://github.com/uber-go/mock) mock, a hand-written fake and an `httptest` server (with benchmarks comparing them), plus an instrumented `Doer` decorator and a probe mode with Prometheus metrics and a Grafana dashboard.

`mockery-of-the-language`: An example to use mockery to generate golang interfances for uses in tests.

`multi-error`: An example of using error wrapping to return multiple errors in a single error.

`rueidis-lua-bench`: SET, GET and add/update/delete Lua scripts through `rueidis` against a single node, a primary or a cluster; the Valkey and Dragonfly examples link it as `bench`.

`struct-embedding`: A basic struct embedding example in Golang.

`sqlc-students`: A basic example of using sqlc to convert sql queries into golang structs.

`threadpool`: A static (fixed-size) worker pool with a bounded task queue, backpressure on submit, context cancellation, graceful drain on close, an optional metrics observer, Prometheus metrics and a provisioned Grafana dashboard.

`titan-prometheus`: A basic example of building a stats/metrics server for a running application using Prometheus.

## Call Visualizer

```sh
go install github.com/ofabry/go-callvis@latest
go-callvis <module-name>
# templated go project
GODEBUG=gotypesalias=1 go-callvis ./sqlc-students/cmd
GODEBUG=gotypesalias=1 go-callvis ./sqlc-students/db/postgres -focus pgqueries
```
