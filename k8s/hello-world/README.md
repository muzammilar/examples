# Kubernetes


**Note:** Due to multiple installations of `kubectl` in parallel, use `which kubectl` (or `gcm kubectl` on Windows) to find the correct `kubectl` version being used.

## Minikube

* Install Minikube with Docker
* Run these
    ```bash
    # start the cluster
    minikube start
    # check nodes
    kubectl get nodes -o wide
    # chekc the dashboard
    minikube dashboard
    # stop and delete
    minikube stop
    minikube delete
    ```
