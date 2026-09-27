# YugabyteDB — 3 masters + 3 tservers on kind with Helm

The official [`yugabytedb/yugabyte`](https://docs.yugabyte.com/stable/deploy/kubernetes/single-zone/oss/helm-chart/)
Helm chart `2026.1.2` (YugabyteDB `2026.1.2.0-b137`) on a one-node kind cluster: StatefulSets
`yb-master` (3) and `yb-tserver` (3), replication factor 3, with small resource requests and
volumes set in [`values.yaml`](values.yaml).

Requires `kind`, `kubectl`, `helm`.

```bash
make up        # kind cluster, helm install, wait until YSQL answers
make test      # run ysql/*.sql (sharding, tablet leaders/replicas, transaction, index) and ycql/*.cql (TTL, JSONB, transactional table + index)
make status    # yb-admin list_all_masters / list_all_tablet_servers, pods and PVCs
make cli       # interactive ysqlsh on yb-tserver-0
make cli-ycql  # interactive ycqlsh on yb-tserver-0
make ui        # port-forward the master UI to http://localhost:7000 and yugabyted UI to http://localhost:15433
make down      # delete the kind cluster
```

`up` is split into `kind-cluster` and `chart`. Yugabyte's docs also list a
[Kubernetes Operator](https://github.com/yugabyte/yugabyte-k8s-operator), but it runs through
YugabyteDB Anywhere; the Helm chart is the standalone path.

- In-cluster: YSQL `yb-tservers.yb-demo:5433`, YCQL `yb-tservers.yb-demo:9042`
- tserver UI: `kubectl --context kind-yugabyte -n yb-demo port-forward yb-tserver-0 9000`

If the image is already in the local Docker, `kind-cluster` copies it into the node
(`docker save --platform` + `ctr import`; `kind load docker-image` fails on this multi-arch
image); otherwise the node pulls ~850 MB on first start. `enableLoadBalancer: false` because
kind has no LoadBalancer. All pods share one kind node, so the chart's soft pod anti-affinity
has no effect and every replica sits on the same host.
