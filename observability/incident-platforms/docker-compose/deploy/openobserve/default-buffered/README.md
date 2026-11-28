# Native default-buffered path — preserved failed recovery

Parent lifecycle: `make bootstrap OPENOBSERVE_SERVICE=openobserve`. This component
uses the original `openobserve` volume. It is retained beside the synchronous
reference; do not delete, reset or relabel its failed evidence.

OpenObserve 1.2.0 defaults `ZO_WAL_FSYNC_DISABLED=true`; this file makes that
original native default explicit. The healthy million-point/million-log/30k-span
projection passed. After a ten-second SIGKILL outage, the partial frozen run
retained all 577,000 metric and 548,000 log ACKs, but **30 of 17,310 span ACKs**
were absent at readback. Requests 90–99 were one accepted OTLP batch whose HTTP 200
preceded exit by about 78 ms. The 120-second continuity gate failed because that
anchor remained missing; later ingestion resumed. No repair replay/reset occurred.

Source-informed buffering is consistent with this observation; physical WAL/index
attribution is not fully isolated, and this is not a statistical loss bound. The
old healthy corpus survived. Original source, journals, failed statuses, native
container state and volume remain available through the parent example's ignored
results and owned resources. See [findings](../../../../docs/FINDINGS.md).
