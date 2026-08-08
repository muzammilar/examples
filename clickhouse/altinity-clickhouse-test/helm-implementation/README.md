# helm-implementation

A small Helm chart wrapping the `ClickHouseInstallation` (CHI) and
`ClickHouseKeeperInstallation` (CHK) custom resources, for testing
`logger/level` and `logger/formatting/type`.

Deploys into its own namespace, `clickhouse-helm-test`, separate from
`kubectl-implementation`'s `clickhouse-kubectl-test` — both can run at once.

## Layout

```
Chart.yaml
values.yaml
templates/
  chi.yaml   # 2x2 ClickHouse cluster (CHI)
  chk.yaml   # 3-node keeper (CHK)
```

## Values

See `values.yaml`:

- `chVersion` / `chkVersion` — ClickHouse / keeper image tags
- `logger.level`, `logger.formattingType` — the settings under test
- `chi.name`, `chi.shards`, `chi.replicas`
- `chk.name`, `chk.replicas`

## Cluster + operator setup

Run once, from this directory. The operator only watches its own install
namespace by default, so it needs to be told about both implementations'
namespaces explicitly via `watchNamespaces`:

```bash
kind create cluster --config ../kind-config.yaml
kubectl config use-context kind-altinity-test

helm repo add clickhouse-altinity-com https://docs.altinity.com/clickhouse-operator/
helm repo update clickhouse-altinity-com

helm install altinity-clickhouse-operator clickhouse-altinity-com/altinity-clickhouse-operator \
  --version 0.27.2 \
  --namespace clickhouse-operator --create-namespace \
  --set 'watchNamespaces[0]=clickhouse-helm-test' \
  --set 'watchNamespaces[1]=clickhouse-kubectl-test'
```

(`watchNamespaces` is a clean top-level Helm value on chart `0.27.2`; on older
chart versions it's buried in a raw `config.yaml` string with no clean
`--set` path — simplest workaround there is deploying into the operator's own
namespace instead.)

## Deploy

```bash
kubectl create namespace clickhouse-helm-test
helm lint .
helm template clickhouse-test . --namespace clickhouse-helm-test   # preview
helm upgrade --install clickhouse-test . --namespace clickhouse-helm-test
```

To test a different version:

```bash
helm upgrade --install clickhouse-test . \
  --namespace clickhouse-helm-test \
  --set chVersion=26.3.9.8 --set chkVersion=26.3.9.8
```

## Verify

```bash
kubectl -n clickhouse-helm-test get chi test -o jsonpath='{.status.status}'
kubectl -n clickhouse-helm-test get chk keeper -o jsonpath='{.status.status}'

kubectl -n clickhouse-helm-test exec chi-test-test-0-0-0 -c clickhouse -- \
  cat /etc/clickhouse-server/config.d/chop-generated-settings.xml | grep -A6 "<logger>"

kubectl -n clickhouse-helm-test exec chi-test-test-0-0-0 -c clickhouse -- \
  tail -n 5 /var/log/clickhouse-server/clickhouse-server.log
```

## Teardown

```bash
helm uninstall clickhouse-test --namespace clickhouse-helm-test
# or tear down the whole cluster:
kind delete cluster --name altinity-test
```
