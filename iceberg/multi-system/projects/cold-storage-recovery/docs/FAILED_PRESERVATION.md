# Preserving the current failed compactor run

The original **3,800,000-row** compactor run remains **failed**: its final
ClickHouse ordered export exceeded the 1 GiB native memory limit. The separate
bounded diagnostic and restored-volume readbacks verified every acknowledged
body; they do not change that operational verdict.

The reviewed plan in `failed-preservation-plans.json` now pins the case, original
source/artifact seals, diagnostic baseline and independently restored proof.
`failed_preservation.py` verifies those inputs without changing the database:

```sh
export FAILED_CASE=results/current/qualification-20261122/live/production/6191b730-b1aa-4cbf-8371-13952cf26a54
make preserve-failed-case
make check-failed-preservation
```

Publication positively checks native stopped-process identities, or absence in a
healthy full runtime listing; an inspection error cannot imply fencing. It also
checks the current native table head against the restored snapshot. Offline
replay checks the latest receipt, complete witness/export hashes, restore/archive
proofs and saved process fences. A newer incomplete attempt blocks replay.
Original source is preserved byte-for-byte; the controller's source is separately
materialized, including only repository-tracked shared-source links.

The final publication and separate offline replay pass **224 controls**, with
**76 original source files, 229 original artifacts, 343 total case artifacts and
16 native process fences**. Both engines' restored proof reconciles **3,800,000
acknowledged rows / 380 batches**, body hash
`f6a5030dc86070c81d9ba72f543bc0b032a8c6eb12869f89ea4110bcc681c2b2`.
The original snapshot remains `1075081727671484799`.

Publication took **9.570 s**, offline replay **7.921 s**, including their unit
controls and evidence rehashing. Host-controller CPU was **9.291 / 7.723 core-s**;
maximum child RSS was **102.7 MiB**. These are bookkeeping costs, not fresh database
performance measurements. Backend CPU/memory, compression, allocated disk and
device read/write bytes/I/O remain in the linked
[`CURRENT_COLD_RESULTS.json`](../reports/CURRENT_COLD_RESULTS.json) restore evidence.
The compact receipt/provenance summary is
[`FAILED_PRESERVATION_RESULTS.json`](../reports/FAILED_PRESERVATION_RESULTS.json).

One intermediate publication refused the example's shared compactor symlink;
its failure and exact controller source remain preserved. The final materializer
accepts repository-tracked source targets only, and still rejects evidence
symlinks, untracked targets and paths outside the repository. Negative controls
also reject corrupted original source/artifacts and unknown runtime state.

This receipt deliberately retains `passed=false` and `teardown_authorized=false`.
**Existing cleanup gates are unchanged.** Integrating freshness/all-history gates
and defining retirement for later native-maintenance fixtures remains unfinished;
do not use this receipt to bypass them or delete the retained volumes. The older
historical fixture's separate deletion-approval requirement also remains intact.
