# Multipart publication boundary workload

This is an application within the parent Garage lab, not a second deployment.
The parent owns its nine nodes, lifecycle, credentials, writer/reader and results.
Its five-command Quick start remains in [the parent README](../README.md).
Run `make multipart-failure` there after `make test` against the same fixture.

`main.py` links to the tracked `object-storage/multipart-workload/main.py` source,
shared with the SeaweedFS protocol case. It uses the existing pinned Boto3 oracle, preserving its explicit local
credentials, one SDK attempt, deadlines and exact-byte verification. This is a
focused extension of the existing Python SDK workload exception. It is not a new
repository-wide harness. `Dockerfile` links to the parent SDK image definition;
`make build` materializes this source as `.build/tools/multipart_workload.py` and
builds it alongside the tracked shared S3 helpers. The `multipart-workload`
Compose service uses that local image. Materialized files remain ignored.

The SDK signs requests. Python's standard HTTP client only controls incomplete
body transmission and response disposal. A healthy raw-transport control must
pass before any fault. This lab permits only its fixed owned `garage0:3900` endpoint. The shared module
also supports the explicitly selected SeaweedFS `s3api0:8333` route; other URLs fail closed.
Four unit controls reject an HTTP-200 error body, require completion XML, prove
response disposal never creates an ACK and reject an unexpected host before I/O.

Each object uses four 8 MiB parts, with fsynced part/ETag and publication records.
The controller verifies the application source hash and the connected peer, holds
197 of 395 completion-body bytes in the measured run, proves native exit 137 and
then restarts the same serving node. Regular PUTs and an independent reader remain
live. A fresh client checks retained parts before completing the same upload ID.

The discarded-response case stops before accepting the completion XML. It remains
ambiguous despite successful independent body readback. It is not replayed or
relabeled an ACK. The native audit explicitly verifies all three copies of this
independently observed publication as well as acknowledged objects. The abort case
checks final-key absence and upload-list disappearance; physical GC is unrun.

Witnesses and result summaries distinguish these boundaries from an internal
metadata-commit crash or a proven network loss before publication. See
[measured results](../reports/MULTIPART_RECOVERY.json) and
[resource diagnostics](../reports/MULTIPART_RESOURCES.json).
