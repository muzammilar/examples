# Multipart recovery follow-up

Extend this lab at Level 3; reuse its nine nodes, independent reader, external
witness and exact-owner audit. Do not create another storage cluster or overwrite
historical 2.5.0 reports. Current stable selection is 2.6.0, pinned by digest.

The pinned [completion implementation](https://github.com/deuxfleurs-org/garage/blob/v2.6.0/src/api/s3/multipart.rs)
loads and validates parts, publishes a final version and block references, then
publishes the complete object before constructing the response. Therefore a
client error or an absent response cannot establish that completion failed. A
partial request-body interruption exercises a different boundary from losing the
response after publication. Keep these as separately reported tests.

1. **Interrupted request and retained parts.** Upload four 8 MiB parts with a
   fsynced external part/ETag ledger. Start a directly routed completion request,
   hold its XML body incomplete and kill that exact owned serving process.
   Prove the process exit and request interruption; do not call this an internal
   metadata-commit crash. Verify that the final key is absent, acknowledged parts
   remain usable and a fresh client can complete the same upload ID after recovery.
2. **Lost completion response.** Transmit the full completion request, deliberately
   discard its response before the application accepts it and record the outcome
   as ambiguous. A fresh client checks the complete object body/metadata before
   any retry. If present, verify its full checksum and classify it as published
   without an application ACK; an HTTP 200 alone is insufficient.
3. **Abort and cleanup.** Abort an incomplete upload, verify the final key remains
   absent and distinguish immediate logical disappearance from delayed physical
   block reclamation. Do not equate abort with overwrite/delete of a completed
   object. Preserve all witnesses until complete reconciliation.

For each case, keep the historical-object reader active, prove fresh reader
progress after recovery, reconcile all attempted keys, and run native repair plus
an exact three-owner/body barrier. Record CPU utilization/core-seconds, memory,
logical/allocated storage, uncompressed body ratio, disk bytes and I/O counts,
latencies, successful/failed/ambiguous operations and any missing measurement.
Retain failures without reseeding. Extend the local native auditor with explicit
multipart part boundaries in its witness; do not assume all multipart payloads
have the original fixture's one hardcoded key.

All three bounded cases above ran on Garage 2.6.0. See
[the source and ownership](../multipart-workload/) and
[the complete measured result](../reports/MULTIPART_RECOVERY.json).
The healthy raw-transport control passed before injection; four negative controls
and a separate history test protect the ACK distinction. Physical abort reclamation
and an internal metadata-commit interruption remain unrun.

The first cleanup failed its immediate post-delete empty-bucket assertion. A later
fresh client observed zero objects and zero pending uploads; a separately invoked
`make down` succeeded. Both attempts remain recorded. This transient listing result
has not been isolated enough to attribute it to a component or consistency model.
