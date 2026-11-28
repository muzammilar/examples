# JuiceFS

JuiceFS separates POSIX clients, transactional metadata and object bodies. The
initial [dependency-recovery lab](dependency-recovery/) uses native C++ file
publication on independent mounts, PostgreSQL metadata and local SeaweedFS RF3.
Metadata-process and S3-gateway outages ran with live readers/writers, durable
witnesses and complete native-copy checks. Good outcomes and rejected probes are
retained; this is a bounded local recovery result, not production qualification.

Planned deployment coverage: a minimal metadata/object/client setup; supported
metadata-HA Compose deployment and independent restore; private-kind CSI mounts
with owned contexts and storage/failure-domain checks. These are TODOs, not tested
examples. The current replicated object store does not make its single metadata
server or gateway highly available. Native metadata and object-store failure
models must be evaluated separately.

Compare file publication with [SeaweedFS](../seaweedfs/filesystem-cluster/) and
[CephFS](../ceph/filesystem-ha/) only after matching semantics, cache state and
resource budgets. Existing diagnostics do not establish a performance winner.

[Upstream architecture](https://juicefs.com/docs/community/architecture/) ·
[Community source](https://github.com/juicedata/juicefs)
