# Enrichment recovery qualification — 2026-11-21

The pending core cases and three diagnostic trials completed on
`kafka-connect-live-recovery`. Measured README/JSON summaries are published on
`main`. The user requested cleanup after publication; the owned infrastructure
and bulky journals have now been removed. Do not try to resume this deleted
fixture or assume every production-test requirement is qualified.

## Verified execution

The ten retained containers and mount identities were checked against the pause
receipt and resumed without replacing their volumes. Docker reordered mount
arrays; identity checks now compare destinations rather than array order.
Independent source/output readers and an SQL writer/reader stayed live through
fault injection, restart, native catch-up and recovered progress. Every new case
replayed all current and earlier acknowledged bodies, keys and partitions.

| Fresh case | Case ID | ACK orders |
|---|---|---:|
| Broker SIGKILL, repaired copy leading all 12 order partitions | `0e7586d867b1402bad522c3daf650cc1` | 2,009,216 |
| Active Connect worker SIGKILL and surviving-worker task takeover | `42c45d7baf8545b78877e5a76c3728e6` | 2,001,024 |
| Processor SIGKILL / transactional resume | `e8d547588d404ddf9e55d3cada940b15` | 2,004,096 |
| PostgreSQL SIGKILL / CDC catch-up | `f889033b00c7415b94b763bfe4163b01` | 2,006,144 |
| Healthy diagnostic 1 | `94f69244ea094f5fbe9b50318cbb7f2c` | 2,002,048 |
| Healthy diagnostic 2 | `7e7c5c21792642eb95d9f3d4dffbe913` | 2,002,048 |
| Healthy diagnostic 3 | `8b364078a09c402cb4ac64dd1914a46a` | 2,009,216 |

All seven passed. Together with the prior baseline and separately reconciled
interrupted broker data, final verification covers **18,038,912 ACKs across nine
data histories**, zero acknowledged loss/corruption. All 1,161 SQL probe ACKs
reconcile. Eight controller tests pass. Native compression was measured from every
sealed source/output partition, with CRC32C validation and maintained LZ4/CRC
libraries; corrupted/truncated copied segments were rejected by negative controls.

`VERIFIED_RESULTS.json` and README contain the exact timings, bytes, image/source
hashes, resource counters, sampling limits and interpretation. The median observed
pipeline rate was 34,589.33 records/s. These are short, buffered, minimum-count
pipeline diagnostics, not sustained capacity, cold-storage or language comparisons.
Raw metrics include CPU, memory, network and block bytes; IOPS, I/O latency and
queue depth were not exposed by this Docker stats API and were not collected.

## Preserved failures

- The original one-order stale-CDC bug remains failed evidence. Its source/output
  fingerprints stayed unchanged through every final check; the fixed processor's
  new cases do not rewrite that bad output.
- Setup `6c20cb050040435fa9ca788577e2df76` remains failed: internal Connect topics
  were RF1. Its separate receipt proves zero order data; generated properties and
  actual RF3/ISR3/lag-zero state were corrected without resetting state.
- Broker `41ac7cec594f484eb9386b098036ed20` remains interrupted/failed. A separate
  post-interruption replay verified 2,001,024 ACKs on both paths, with zero loss or
  corruption. The original failure and later receipt have separate hashes.
- Initial teardown failed its post-check because Compose left six anonymous
  Connect volumes. Exact saved ownership plus positive empty runtime references
  allowed separate manual cleanup. Do not call the original teardown passing.

## Cleanup and remaining work

Cleanup removed 129 owned containers and 25 volumes, including those six anonymous
volumes; nine foreign containers and ten foreign volumes retained their identities
and states/metadata. No example-built image tags remain. User-requested removal
of 145 large journal/log-copy paths reclaimed 5.48 GiB allocated; about 60.3 MiB
of reports, SQL journals, metrics, diagnostics and proof summaries remain locally.
The native data and large ACK/read journals no longer exist. Hashes do not restore
them. Relevant receipts are under ignored `results/resume-20261121/` and
`results/cleanup-20261121.json`; published summaries survive in Git.

A fresh fixture was then created for lifecycle qualification. The
[lifecycle summary](../reports/LIFECYCLE_RESULTS.json) records a new 2,001,024-ACK baseline,
ten unchanged service identities on corrected reapply, retained application
configuration/recreation and full replay after native service stop/start.
Four failed prototype checks remain failed evidence: unnecessary image rebuild,
lost removed-processor configuration and two overly strict shutdown checks.
The corrected harness passed 23 controller tests and removed all owned resources.

A second empty fixture passed the final five commands serially without retries,
using `COUNT=1000000`: **1,001,024 ACK orders**, zero acknowledged loss/corruption,
25 owned volumes removed, nine foreign containers and ten foreign volumes
unchanged. Both new fixtures preserve their external journals and reports under
ignored results; the earlier fixture is archived by owner ID. The source and
measured summaries accompany this qualification. No live fixture remains.

Individual core cases and the three-trial target ran serially; the single
production aggregate invocation was not rerun. Extended disk/partition/quorum,
scale-out/in, vertical changes, pressure, upgrades, independent restore and fair
batch/skew/hot/warm/cold/sustained comparisons remain unrun. Do not infer them from
the passing core cases or lifecycle qualification.
