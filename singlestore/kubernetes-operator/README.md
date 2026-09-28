# SingleStore — cluster on kind with the SingleStore Kubernetes Operator

The [SingleStore Kubernetes Operator](https://docs.singlestore.com/db/v9.0/deploy/kubernetes/)
`4.176.0` managing a `MemsqlCluster` ([`sdb-cluster.yaml`](sdb-cluster.yaml)) of
`singlestore/node:alma-9.0.44-1322a9e639` (SingleStore 9.0.44): 1 master aggregator,
1 child aggregator and 2 leaves in a one-node kind cluster. The CRD, RBAC and operator
Deployment are the ones from the deployment guide (SingleStore publishes no Helm chart).

Requires `kind`, `kubectl` and a license key:

```bash
export SINGLESTORE_LICENSE='<license key>'
make up       # kind cluster, CRD + RBAC + operator, license Secret, MemsqlCluster; waits for phase Running
make test     # run sql/*.sql on the master aggregator: reference / rowstore / columnstore tables with
              # SHARD KEY + SORT KEY, 50k customers + 600k orders generated server-side, colocated vs
              # broadcast join (EXPLAIN), PROFILE, segment compression + block elimination, JSON,
              # VECTOR <*> / <->, SHOW AGGREGATORS / LEAVES / PARTITIONS, rows per partition
make status   # MemsqlCluster + pods, aggregators, leaves, partitions per leaf
make cli      # interactive client on the master aggregator (user admin)
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `operator`, `cluster`. The first `up` pulls the ~1.4 GB
`singlestore/node` image inside kind, which took 15 to 100+ minutes on a
busy network; `make cluster` waits up to 60 minutes and can simply be re-run.

## License

The operator rejects a `MemsqlCluster` without one (`either spec.license or
spec.licenseSecret must be specified`). `make cluster` stores `$SINGLESTORE_LICENSE` in the
Secret `singlestore-license`, referenced by `spec.licenseSecret`. Get a key (e.g. the
30-day Enterprise Trial) from the [Cloud Portal](https://portal.singlestore.com).

[Setting or Replacing a License](https://docs.singlestore.com/db/v9.0/user-and-cluster-administration/maintain-your-cluster/setting-or-replacing-a-license/)
says "SingleStore no longer requires a license for development, prototyping, and functional
testing when using SingleStore Free Edition or the SingleStore Dev Image", but that does not
cover a multi-node cluster:

- The "Free Edition" is the Dev Image. [SingleStore Editions](https://docs.singlestore.com/db/v9.0/introduction/singlestore-editions/)
  lists Standard, Enterprise and a free Dev Image ("maximum capacity of 8 vCPUs and 64GB of
  RAM ... deployed anywhere as an integrated container image"), and the
  [Self-Managed pricing FAQ](https://www.singlestore.com/pricing/?product=Self-Managed) says
  "SingleStore offers a free Developer Image which can be used for testing or local
  development". No other license-free self-managed download exists.
- The Dev Image is "one Master Aggregator and one leaf node ... without the need for a
  license" ([README](https://github.com/singlestore-labs/singlestoredb-dev-image#how-to-run-the-docker-image)).
  It does not skip the license. It bakes one in at build time (`BOOTSTRAP_LICENSE` in its
  Dockerfile), and that license is the "Developer Image Edition".
- The engine still needs a key. On a bare `singlestore/node` container (the image the
  operator runs) with no license, `BOOTSTRAP AGGREGATOR` fails with `Error 2446: A valid
  license is required to run BOOTSTRAP AGGREGATOR`, and `SET LICENSE = ''` fails with
  `Error 1888: The provided license is invalid`. The operator, `sdb-deploy`/Toolbox and plain
  containers all hit the same check. `sdb-deploy cluster-in-a-box` is deprecated in favour of
  the Dev Image.
- The Dev Image key is accepted but has its own limits. Adding a child aggregator or a second
  leaf fails with `Error 2633: Feature 'child aggregators' / 'more than 1 leaf' is not
  supported in SingleStore Developer Image Edition`. The master aggregator raises this when
  the operator runs `ADD AGGREGATOR`/`ADD LEAF`, so any other deployment tool hits it too.
  Two leaves (and a child aggregator) therefore need a Standard,
  Enterprise or Enterprise Trial key.

With the Dev Image key, shrink the cluster to a master aggregator + one leaf:

```bash
SINGLESTORE_CHILD_AGGREGATORS=0 SINGLESTORE_LEAVES=1 make up
```

That license also caps every database at 2 partitions (instead of
`default_partitions_per_leaf: 4` per leaf from `sdb-cluster.yaml`).

## Notes

- `redundancyLevel: 1`: both leaves in one availability group. With `redundancyLevel: 2`
  the operator puts each leaf in its own availability group (partition masters on one,
  replicas on the other) with required pod anti-affinity, which needs 2+ Kubernetes nodes.
- Nodes are sized at 1-2 cores and 2-3 GiB (production guidance is 4-8 cores and 32-64 GiB
  per node); license units are counted from the leaves' `cores`/`memoryMB`.
- `serviceSpec.type: ClusterIP`: the default `LoadBalancer` DDL service never gets an address
  on kind and blocks the operator.
- The operator's recommended sysctl/THP DaemonSet (`vm.max_map_count`, `vm.min_free_kbytes`,
  transparent huge pages) is not applied: on Docker Desktop it would change the whole Docker VM.
- Both images are amd64 only. The operator image is an image index without an arm64 entry, so
  [`sdb-operator.yaml`](sdb-operator.yaml) pins its `linux/amd64` manifest digest; on an arm64
  kind node (Apple silicon) both run under Docker Desktop's Rosetta emulation.
- SingleStore's own docs recommend the [Dev Image](../single-node) rather than the operator for
  local development.
