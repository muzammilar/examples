# Multipart publication through a stateless S3 gateway

Part of this SeaweedFS lab, not a second deployment. Use the parent's five-command
Quick start, then `make multipart-failure` in the same example and `RESULTS_DIR`.
The parent owns all nodes, credentials, state, witnesses, results and cleanup.

`main.py` links to tracked [protocol source](../../../object-storage/multipart-workload/).
`Dockerfile` links to this example's SDK image definition. `make build` copies
regular source files into ignored `.build/tools/`; there is no dependency on
another example's generated files or running services. The `multipart-workload`
Compose service explicitly selects its owned `http://s3api0:8333` endpoint.
The existing pinned Boto3 oracle is retained as the documented Python SDK exception.

Four 8 MiB parts exercise a healthy raw-transport control, incomplete completion
request with exact serving-gateway SIGKILL, recovery/completion with the same upload
ID, discarded completion response and abort visibility. Regular writer/reader
containers stay live. The source hash and connected peer are checked before
injection. Every successful publication gets complete independent body readback;
client ACKs and the observed-but-unacknowledged publication remain distinct.

SeaweedFS's native filer metadata supplies chunk IDs/offsets. Direct volume reads
with `?proxied=true` verify each zone copy, including the discarded-response object.
A wrong-owner read must return 404, preventing transparent proxying from masquerading
as a local copy. Three etcd members must agree at the same current revision.

This is an S3 **gateway** loss test. It is not an internal filer transaction crash,
data-disk loss, metadata quorum loss or physical power-loss proof. Discarding a
response after headers is not proven packet loss before publication. Abort physical
reclamation remains unrun. See [measured results](../reports/MULTIPART_RECOVERY.json)
and [resource measurements](../reports/MULTIPART_RESOURCES.json).
