# Kubernetes


## Setting up k8s on Windows (for Development)
As of August 2021, some useful links/methods/options to setup k8s for development purposes.
* Vagrantfiles and VMs
* WSL (and Docker):
    * Docker Desktop:
        * This generally installs its own version of `kubectl`
    * Minikube (with Docker):
        * Using `winget` to install minikube: https://minikube.sigs.k8s.io/docs/start/
    * Rancher Desktop: https://rancherdesktop.io
    * microk8s:
        * https://ubuntu.com/blog/kubernetes-on-windows-with-microk8s-and-wsl-2
    * KinD:
        * https://kubernetes.io/blog/2020/05/21/wsl-docker-kubernetes-on-the-windows-desktop/
    * K3d (K3s on Docker): https://k3d.io/
