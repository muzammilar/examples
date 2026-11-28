# Pre-load qualification findings

These happened before application seeding. They are deployment/controller
findings, not evidence of database corruption or failed application recovery.
Raw startup logs and the empty-table/input checks remain ignored in `results/`.

- The first configuration made every BE wait for all three healthy FEs. Native
  follower startup waited, and FE logs showed version-comment queries refusing
  work because no backend existed. Starting the three owned BEs broke the cycle.
  BEs now depend only on the first FE; full membership is a later semantic gate.
  This observation does not isolate a Doris query-engine defect.
- Rebuilding/restarting retained voting FEs exposed another readiness cycle:
  followers waited for a healthy first FE, while its retained metadata needed a
  voting quorum. Starting the retained follower containers directly recovered it
  without clearing metadata. Followers now depend on first-FE **start**, allowing
  quorum members to start together. Native membership, not creation order,
  establishes readiness.
- The semantic startup checker initially parsed Compose diagnostics as JSON and
  timed out despite three live FEs/BEs and 18 current tablet replicas. It now
  filters non-JSON diagnostics and still rejects malformed structured output.
  The failed phase remains visible in curated provenance.
- The pinned ARM64 `spark:4.3.0-scala2.13-java21-python3-ubuntu` distribution
  (`sha256:e1831ac4e26f91c80de8c360d7853ff507b08934ed0974a2823ec7080f73f3c7`)
  reported Python **3.10.12**. Spark 4.3 requires **3.11+**. It was rejected before
  workload execution. The corrected Dockerfile copies its Spark distribution
  (including Hadoop 3.5.0 client JARs) into pinned Temurin 17/Noble and installs
  Noble's supported Python 3.12. Java 17 is supported by Spark 4.3. No performance
  comparison between these runtimes is claimed.

[Supported Spark 4.3 runtimes](https://spark.apache.org/docs/4.3.0/),
[Doris load-majority semantics](https://doris.apache.org/docs/4.x/data-operate/import/load-best-practices/load-high-availability/).

- Compose config without `--profile tools` omitted workload-only services from the
  initial budget header/preflight. The unchanged actual complete budget was
  **10 CPUs / 20.75 GiB**, and still fit Docker plus the reserved 2 GiB headroom.
  The original 8 CPU / 17.75 GiB persistent-role header is retained with an explicit
  correction derived from all-profile configuration and recorded actor limits.
  Future preflights include every profile; no resource allocation was reduced.
