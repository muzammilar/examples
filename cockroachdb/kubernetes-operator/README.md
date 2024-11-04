# Hello Roach

A simple CI script to install and setup cockroachdb in k8s.

#### Pre-Requisites

```sh
# source: https://github.com/cockroachdb/cockroach-operator

# Apply the custom resource definition (CRD) for the Operator
kubectl apply -f https://raw.githubusercontent.com/cockroachdb/cockroach-operator/master/install/crds.yaml

# Apply the Operator manifest
kubectl apply -f https://raw.githubusercontent.com/cockroachdb/cockroach-operator/master/install/operator.yaml

# list k8s namespaces in your cluster
kubectl get namespaces

# list all pods
kubectl get pods

```

#### Installation

##### Cockroach Cluster
```sh
# See all examples here: https://github.com/cockroachdb/cockroach-operator/tree/master/examples

# Apply example.yaml
kubectl create -f example.yaml

#Check that the pods were created
kubectl get pods
```

##### Client

```sh
# Launch a secure pod running the cockroach binary.
kubectl create -f https://raw.githubusercontent.com/cockroachdb/cockroach-operator/master/examples/client-secure-operator.yaml

# Get a shell into the client pod
kubectl exec -it cockroachdb-client-secure -- ./cockroach sql --certs-dir=/cockroach/cockroach-certs --host=cockroachdb-public

# list relations
\dt+
```

##### Cleanup

```sh
# Delete the custom resource
kubectl delete -f example.yaml

# Remove the Operator
kubectl delete -f https://raw.githubusercontent.com/cockroachdb/cockroach-operator/master/install/operator.yaml


```
