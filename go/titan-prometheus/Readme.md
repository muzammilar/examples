# Titan

A basic example of building a stats/metrics server for a running application using Prometheus. Titan generates metrics/stats that are aggregated by a stats server routine and exposed to Prometheus.

```sh
docker-compose up --build

```

Titan metrics are exposed locally on `localhost:18080/metrics` for the user (since port `8080` on the guest is mapped to `18080` for the host).

## Grafana

Grafana runs by default on port `3000`. Check the details about Grafana docker [here](https://hub.docker.com/r/grafana/grafana).


## Prometheus

Prometheus is available by default on port `9090`. Check the details about Prometheus docker [here](https://hub.docker.com/r/prom/prometheus).

### Prometheus Best Practices

Read [Prometheus Best Practices](https://prometheus.io/docs/practices/) guide for best practices.

#### Labels (or Not)

* As defined in the [docs](https://prometheus.io/docs/practices/naming/#labels):

  ```
  Remember that every unique combination of key-value label pairs represents a new time series,
  which can dramatically increase the amount of data stored.
  Do not use labels to store dimensions with high cardinality (many different label values),
  such as user IDs, email addresses, or other unbounded sets of values."
  ```

#### Counters

* All values are floats so use seconds as the unit for time (instead of milliseconds or microseconds).
Handle the visualization on Grafana or other visualization layer.

* Use `rate()` function instead of `increase()` since it's syntactic sugax for `rate()`

* Use `irate()` to find the increase in rate over time. It provides a good average. For a failed scrape, it will be lower.

#### Summary
* Using quantiles is slower since it requires mutexes and some approximation to compute.
