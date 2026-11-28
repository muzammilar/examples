# GreptimeDB versus Victoria metrics and logs

Focused performance depth **3**, agreed for this batch. The first healthy
controls passed at one million metrics and one million logs per engine/protocol,
with verified lifecycle cleanup. Live crash checks now expose buffered Victoria
ACK loss and demonstrate external-witness repair; Greptime retained its synchronous
WAL ACKs. [Recovery evidence](single-node/docs/RECOVERY.md) preserves the failure.
The selected level-3 single-node pass now includes three fresh-state paced
trials and correct first/warm queries. [Measured comparison](single-node/docs/COMPARISON.md)
reports lower Victoria costs for this workload and the unequal durability contracts;
there is no overall winner. Clustered coverage remains future work.

- `single-node/`: equal whole-backend budget, GreptimeDB versus VictoriaMetrics +
  VictoriaLogs. Common OTLP protobuf ingestion, native ingestion separately,
  complete value/body verification, ingestion batches and cold/warm queries.
- Cluster comparison: pending contract review. GreptimeDB's shared object storage,
  metadata and WAL must fit the same total budget as Victoria's routers, storage
  and replication. VictoriaLogs replication/fanout semantics must be stated
  separately rather than equating redundant delivery with consensus.
- `telemetry-workload/`: comparison-owned native Go corpus, workload and oracle.
  Each runnable example exposes this tracked source through a local descriptive
  symlink and materializes it into its ignored `.build/` before Docker builds.
- `docs/`: protocol, resource and fairness decisions. Raw results are ignored;
  curated numeric outcomes belong in the runnable example's `reports/`.

The existing Greptime examples have useful functional evidence but unequal total
budgets. Their numbers do not decide this comparison. Three-signal and incident
stack comparisons are separate projects in the requested batch.
