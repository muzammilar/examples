# Altinity ClickHouse operator logger settings test

Run in order:

```bash
make kind-cluster            # once — creates the kind cluster + installs the operator
make helm-implementation     # and/or
make kubectl-implementation  # deploys into its own namespace, so both can run side by side
```

Teardown, in reverse:

```bash
make delete-helm-implementation
make delete-kubectl-implementation
make delete-kind-cluster
```

See [`Makefile`](Makefile) for details. Per-implementation docs:
[`helm-implementation/`](helm-implementation/README.md), [`kubectl-implementation/`](kubectl-implementation/README.md).
