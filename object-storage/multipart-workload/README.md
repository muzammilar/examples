# Shared multipart protocol workload source

Application source only. [Garage](../../garage/s3-cluster/multipart-workload/)
and [SeaweedFS](../../seaweedfs/s3-cluster/multipart-workload/) expose this tracked
file through local symlinks and materialize it as a regular file into their own
ignored Docker build contexts. Each parent lab owns its image, infrastructure,
static credentials, fault controller, witnesses, native replica checks and cleanup.
No example uses another lab's generated files, Makefile or running services.

The same four-8-MiB-part workload checks healthy completion, an incomplete
completion body interrupted by serving-process loss, a deliberately discarded
completion response and abort visibility. SDK signatures and the existing shared
byte oracle are reused. Standard HTTP controls only the transport boundary;
publication errors remain ambiguous until independent readback.

The default route is Garage's owned `garage0:3900`; the SeaweedFS Compose service
explicitly selects `MULTIPART_ENDPOINT=http://s3api0:8333`. Those two exact local
URLs are allowlisted. SDK credential discovery and public endpoint fallback are
not used. Neither protocol source nor shared libraries own global lab state.
