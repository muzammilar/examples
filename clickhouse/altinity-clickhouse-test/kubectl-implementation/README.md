# kubectl-implementation

Plain Kubernetes manifests for the CHI/CHK, applied directly with
`kubectl apply` — no Helm involved. Image versions are template variables
rendered with `envsubst`.

Deploys into its own namespace, `clickhouse-kubectl-test`, separate from
`helm-implementation`'s `clickhouse-helm-test` — both can run at once.

## Layout

```
chi.yaml.tmpl    # CHI template, ${CH_VERSION} placeholder
chk.yaml.tmpl    # CHK template, ${CHK_VERSION} placeholder
versions.yaml    # ch_version / chk_version values
render.sh        # renders the .tmpl files into chi.yaml / chk.yaml
chi.yaml         # generated — do not hand-edit
chk.yaml         # generated — do not hand-edit
```

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
kubectl create namespace clickhouse-kubectl-test
./render.sh
kubectl apply -f chk.yaml
kubectl apply -f chi.yaml
```

To test a different version, edit `versions.yaml`, re-run `./render.sh`,
then re-apply — the operator does a rolling upgrade in place.

## Verify

```bash
kubectl -n clickhouse-kubectl-test get chk keeper -o jsonpath='{.status.status}'
kubectl -n clickhouse-kubectl-test get chi test -o jsonpath='{.status.status}'

kubectl -n clickhouse-kubectl-test exec chi-test-test-0-0-0 -c clickhouse -- \
  cat /etc/clickhouse-server/config.d/chop-generated-settings.xml | grep -A6 "<logger>"

kubectl -n clickhouse-kubectl-test exec chi-test-test-0-0-0 -c clickhouse -- \
  tail -n 5 /var/log/clickhouse-server/clickhouse-server.log
```

## Teardown

```bash
kubectl delete -f chi.yaml -f chk.yaml
# or tear down the whole cluster:
kind delete cluster --name altinity-test
```
