# Kubernetes


## Setting up k8s on Windows (for Development)
As of August 2021, some useful links/methods/options to setup k8s for development purposes.
* Vagrantfiles and VMs
    * microk8s:
        * https://ubuntu.com/tutorials/install-microk8s-on-windows
* WSL (and Docker):
    * Docker Desktop:
        * This generally installs its own version of `kubectl`
        * It's a very simple way to get k8s running for development.
    * Minikube (with Docker):
        * Using `winget` to install minikube: https://minikube.sigs.k8s.io/docs/start/
    * Rancher Desktop:
        * https://rancherdesktop.io
    * microk8s:
        * https://ubuntu.com/blog/kubernetes-on-windows-with-microk8s-and-wsl-2
    * KinD:
        * https://kubernetes.io/blog/2020/05/21/wsl-docker-kubernetes-on-the-windows-desktop/
    * K3d (K3s on Docker): https://k3d.io/
* ContainerD (deployment):
    * https://containerd.io/downloads/
* Helm (package manager):
    * Helm charts are useful ways to configure k8s applications.
    * https://helm.sh/docs/intro/install/
* Rancher:
    * Docker: https://ranchermanager.docs.rancher.com/pages-for-subheaders/rancher-on-a-single-node-with-docker
    * Rancher Desktop: https://docs.rancherdesktop.io/how-to-guides/rancher-on-rancher-desktop/
    * On Windows, you can use `findstr` instead of `grep`
    * URL: https://localhost:8443/dashboard/auth/login
