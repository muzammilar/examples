# OceanBase on kind with ob-operator

[ob-operator](https://github.com/oceanbase/ob-operator) `2.3.4` (Helm chart `ob-operator/ob-operator`;
2.3.4 is still the latest release, from January 2026) on a two-node kind cluster. It runs an
[`OBCluster`](obcluster.yaml) with **one zone and one observer** in standalone mode
(`oceanbase/oceanbase-cloud-native:4.4.2.1-101000022026050611`, the same OceanBase CE as the
Docker Compose examples, native arm64) and an [`OBTenant`](obtenant.yaml) `test` (MySQL mode,
1 CPU / 1.5Gi). cert-manager `v1.21.2` issues the webhook certificate. The observer is smaller than
ob-operator normally accepts. See [Resource floors](#resource-floors).

Requires `kind`, `kubectl`, `helm` (the repo's `flake.nix` dev shell has them).

```bash
make up       # kind (image side-loaded), cert-manager, ob-operator + lowered floors, OBCluster, OBTenant, client pod
make test     # sql/*.sql through a mariadb client pod: a partitioned table in `test`, then cluster views as root@sys
make status   # OBCluster / OBZone / OBServer / OBTenant, pods, PVCs
make cli      # mariadb shell as root@test (make cli-sys: root@sys)
make down     # delete the kind cluster
```

`up` is split into `kind-cluster`, `cert-manager`, `operator`, `cluster`, `tenant`, `client`.
Passwords are in [`secrets.yaml`](secrets.yaml) (example values). Clients connect to the
Service `obcluster-standalone-svc:2881`, which ob-operator creates in standalone mode.

## Run

2026-10-03, Docker Desktop 29.5.3 on an Apple M4 Pro (Docker VM 11 CPUs, 24.4 GB), kind 0.32.0,
Helm 4.3.0, with the observer image pulled into local Docker beforehand:

| Step | Time |
|---|---|
| `kind-cluster` + `cert-manager` (incl. side-loading the 1.7 GB image) | 73 s |
| `helm install ob-operator` (`--wait`) | 52 s |
| `cluster`: OBCluster `new` → `running` (check-fs and get-version jobs, observer pod, bootstrap) | 131 s |
| `tenant` + `client` (OBTenant `running` in 29 s, then the mariadb pod pull) | 106 s |

`make test` output (abridged):

```
+---------+------+--------+     +------------+----------------+-----------+-------+-----------+--------+
| version | db   | user   |     | TABLE_NAME | PARTITION_NAME | TABLET_ID | LS_ID | SVR_IP    | ROLE   |
| 4.4.2.1 | NULL | root@% |     | orders     | p0..p3         | 200001..4 |  1001 | 127.0.0.1 | LEADER |
+---------+------+--------+     +------------+----------------+-----------+-------+-----------+--------+
| TENANT_ID | TENANT_NAME | TENANT_TYPE | STATUS | LOCALITY      |
|         1 | sys         | SYS         | NORMAL | FULL{1}@zone1 |
|      1001 | META$1002   | META        | NORMAL | FULL{1}@zone1 |
|      1002 | test        | USER        | NORMAL | FULL{1}@zone1 |
| SVR_IP    | CPU_CAPACITY | CPU_ASSIGNED | mem_capacity_gb | mem_assigned_gb | data_disk_gb | log_disk_gb |
| 127.0.0.1 |           16 |            4 |             3.0 |             2.5 |         11.0 |        11.0 |
| NAME                  | MAX_CPU | memory_gb | log_disk_gb |
| sys_unit_config       |       3 |      1.00 |         2.0 |
| unitconfig_test_zone1 |       1 |      1.50 |         2.0 |
memory_limit 4G, system_memory 1G, cpu_count 16, datafile_size 2G, datafile_maxsize 11G, log_disk_size 11G
```

Observed usage: the kind worker (observer, operator, cert-manager, client) used 3.7 GiB and the
control plane 1.0 GiB. On disk the redo-log PVC took 12 GB (`log_disk_size` 11G, preallocated),
the data PVC 2.1 GB and the log PVC 387 MB. The observer pod requests and limits `cpu: 2,
memory: 5Gi`. `CPU_CAPACITY` is 16 anyway, because ob-operator passes `cpu_count = max(cpu, 16)`.

## Resource floors

ob-operator's validating webhook rejects small observers. With the chart's defaults, the
`obcluster.yaml` here gets:

```
The OBCluster "obcluster" is invalid:
* spec.observer.storage.dataStorage.size: Invalid value: "12Gi": The minimum data storage size of OBCluster is 30Gi
* spec.observer.storage.redoLogStorage.size: Invalid value: "12Gi": The minimum redo log storage size of OBCluster is 30Gi
* spec.observer.storage.logStorage.size: Invalid value: "2Gi": The minimum log storage size of OBCluster is 10Gi
* spec.observer.resource.memory: Invalid value: "5Gi": The minimum memory size of OBCluster is 8Gi
```

These are only defaults. The operator reads them from its viper config
(`resource.minMemorySize`, `minDataDiskSize`, `minRedoLogDiskSize`, `minLogDiskSize` in
[`internal/config/operator/default.go`](https://github.com/oceanbase/ob-operator/blob/2.3.4/internal/config/operator/default.go)),
and the environment overrides them as `OB_OPERATOR_RESOURCE_MIN*`. The Helm chart has no value for
the manager's environment, so `make operator` sets them after the install:

```bash
kubectl -n oceanbase-system set env deploy/oceanbase-controller-manager \
  OB_OPERATOR_RESOURCE_MINMEMORYSIZE=4Gi OB_OPERATOR_RESOURCE_MINDATADISKSIZE=12Gi \
  OB_OPERATOR_RESOURCE_MINREDOLOGDISKSIZE=12Gi OB_OPERATOR_RESOURCE_MINLOGDISKSIZE=2Gi
```

The rejection message always prints the built-in floors, even when they are lowered. Some limits
still apply after that:

- **Data and redo log storage must each be at least 3 × `memory_limit`.** If `memory_limit` is not
  set in `spec.parameters`, the mutating webhook sets it to 90% of `resource.memory`. Here
  `memory_limit: 4G` is explicit, so 12Gi is the smallest data and redo size the webhook accepts.
- **The observer preallocates 95% of the redo log size as `log_disk_size`, and 20% of the data size
  as `datafile_size`.** `log_disk_size`, `datafile_size` and `cpu_count` are on the operator's
  reserved list (`ReservedParameters`), so `spec.parameters` cannot override them. local-path
  does not enforce PVC sizes, but the preallocated files are real disk usage: 3 × `memory_limit` × 0.95
  of redo per observer.
- **`memory_limit` 4G** with `system_memory` 1G and `__min_full_resource_pool_memory` 1G
  (default 5G, which does not fit) is the same setting the Docker Compose scale-out example runs its
  observers with. ob-operator's own quickstart uses 10Gi memory and 50Gi data and redo storage.

**3 zones × 1 observer: not run.** Memory would fit (3 × 5Gi requests on a 24 GB VM, about
3–3.7 GiB resident each). Disk would not: each observer needs at least 11 GB of preallocated log disk
plus 2 GB of data file, about 40 GB for three. The Docker VM had 34 GB free at the time. The
topology change is `topology: [{zone: zone1, replica: 1}, {zone: zone2, ...}, {zone: zone3, ...}]`
with the standalone annotation removed (standalone is for a single observer), plus a pool per zone
in `obtenant.yaml`.

## Known issues

- Floors: see above. Without the `set env` step, the exact rejection is the four lines quoted there.
- Right after `kubectl set env` rolls the manager pod, an OBCluster apply can fail with
  `failed calling webhook "mobcluster.kb.io": ... context deadline exceeded`. `make operator` retries
  a server-side dry run of `obcluster.yaml` until the webhook answers.
- `kind load docker-image` fails on the multi-arch observer image with
  `ctr: content digest sha256:...: not found` (it imports `--all-platforms`). `make kind-cluster`
  instead runs `docker save --platform linux/<docker arch> | ctr images import` on the worker.
- The observer image has no `obclient`. `make test` uses a `mariadb:11.4` client pod, which needs
  `--skip-ssl` (otherwise `ERROR 2026: TLS/SSL error: SSL is required, but the server does not
  support it`).
- ob-operator's file-system check job leaves its PVC `obclustercheck-clog-claim-*` in `Terminating`
  (finalizer `kubernetes.io/pvc-protection`) until the cluster is deleted. It holds no data.
- Standalone mode: the observer registers as `127.0.0.1` (`DBA_OB_SERVERS`), so it cannot be
  joined by more observers. A multi-observer cluster needs the default mode, or `service` mode
  (one Service per observer, OceanBase ≥ 4.2.3).
