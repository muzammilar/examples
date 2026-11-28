# Three-signal checkout incident comparison

**Full Greptime and Grafana healthy cases have passed**; recovery is in progress. Performance depth 3.
Greptime/Grafana pass the typed control; Victoria retains the values but its
experimental Tempo API fails numeric attribute-type fidelity. [Observed findings](docs/FINDINGS.md).
The original Victoria full case stopped on a reader sampling error; its partial
ACKs all reconciled, with changed numeric attribute types explicitly retained. The
corrected full case reconciled all ACK values; collector recovery is in progress.
Compare GreptimeDB, VictoriaMetrics/Logs/Traces, and Prometheus/Loki/Tempo using
one deterministic checkout-incident corpus and independent native-API readers.
The [metrics/log comparison](../greptime-victoria/) is separate: its completed
recovery evidence does not qualify traces or collectors here.

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
