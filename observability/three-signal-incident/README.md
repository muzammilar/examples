# Three-signal checkout incident comparison

**Restart qualification failed:** Loki omits 44,274 previously verified log ACKs
after retained stop/start. No collector fault was injected into Grafana; data
remains preserved. [Failure evidence](docs/FINDINGS.md).

Performance depth **3**. Each full healthy case reconciled **1 million metrics,
1 million logs and 30,000 spans**. Greptime and Grafana preserved the selected
typed fields; Victoria's experimental Tempo API changed integer attributes to
strings, so its exact-type qualification remains failed.

Greptime collector recovery passed. Victoria collector values reconciled with the
same type failure. Their sampled all-signal recovery times were **11.873 s** and
**39.011 s**, including a ten-second outage; Grafana's collector case was blocked
by the later Loki restart failure. All ten services are stopped with data retained.
[Measured findings and remaining gaps](docs/FINDINGS.md) preserve every failed run,
including the original reader sampling error. No whole-stack winner is qualified.

This lab compares one deterministic checkout corpus through independent native
readers. The completed [metrics/log comparison](../greptime-victoria/) is separate
and does not qualify these trace, collector or restart paths.

- `incident-workload/`: comparison-owned Go source and official OTLP protobufs.
- `docs/CONTRACT.md`: data, admission/persistence, comparison and recovery design.
- `docker-compose/`: example-owned profiles, persistent collector queues,
  lifecycle, controllers, raw ignored evidence and compact curated reports.

The intended whole-backend budget is 6 CPU / 8 GiB including the collector,
plus the same 1 CPU / 512 MiB workload on each profile. Runtime remains on an
internal Docker network with no external endpoints, cloud credentials or MinIO.
Current capability boundaries: Greptime's trace documentation marks the feature
experimental; VictoriaTraces is a 0.x release. Neither receives a production
maturity claim merely by appearing beside Prometheus/Loki/Tempo.
