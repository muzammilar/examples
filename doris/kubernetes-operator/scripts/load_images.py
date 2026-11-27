"""Import only the Docker VM's native platform into one owned kind worker."""

import json
import subprocess
import sys


def main():
    node = sys.argv[1]
    if node not in [
        "doris-kubernetes-operator-" + s for s in ("worker", "worker2", "worker3")
    ]:
        raise ValueError("Expected an owned worker name")
    metadata = json.loads(
        subprocess.check_output(["docker", "inspect", node], text=True)
    )[0]
    if (
        metadata["Config"]["Labels"].get("io.x-k8s.kind.cluster")
        != "doris-kubernetes-operator"
    ):
        raise ValueError("Worker does not belong to this example")
    arch = subprocess.check_output(
        ["docker", "info", "--format", "{{.Architecture}}"], text=True
    ).strip()
    platform = {
        "aarch64": "linux/arm64",
        "arm64": "linux/arm64",
        "x86_64": "linux/amd64",
        "amd64": "linux/amd64",
    }.get(arch)
    if platform is None:
        raise ValueError("Unqualified Docker VM architecture: " + arch)
    images = [
        "apache/doris:fe-4.0.8",
        "apache/doris:be-4.0.8",
        "examples/doris-operator-orders:4.0.8",
    ]
    complete = True
    keys = (
        "Env",
        "Entrypoint",
        "Cmd",
        "User",
        "WorkingDir",
        "Labels",
        "Volumes",
        "ExposedPorts",
        "StopSignal",
        "Healthcheck",
    )
    for image in images:
        source = json.loads(
            subprocess.check_output(["docker", "image", "inspect", image], text=True)
        )[0]
        cached = subprocess.run(
            ["docker", "exec", node, "crictl", "inspecti", "docker.io/" + image],
            capture_output=True,
            text=True,
        )
        if cached.returncode:
            complete = False
            break
        spec = json.loads(cached.stdout)["info"]["imageSpec"]
        if spec["rootfs"]["diff_ids"] != source["RootFS"]["Layers"] or any(
            spec.get("config", {}).get(k) != source["Config"].get(k) for k in keys
        ):
            complete = False
            break
    if complete:
        print(
            json.dumps(
                {
                    "node": node,
                    "platform": platform,
                    "images": images,
                    "import": "reused verified layers/config",
                }
            )
        )
        return
    exporter = subprocess.Popen(
        ["docker", "image", "save", "--platform", platform, *images],
        stdout=subprocess.PIPE,
    )
    try:
        imported = subprocess.run(
            [
                "docker",
                "exec",
                "--privileged",
                "-i",
                node,
                "ctr",
                "--namespace",
                "k8s.io",
                "images",
                "import",
                "--platform",
                platform,
                "--digests",
                "--snapshotter=overlayfs",
                "-",
            ],
            stdin=exporter.stdout,
        )
        exporter.stdout.close()
        export_status = exporter.wait()
        if imported.returncode or export_status:
            raise RuntimeError(
                f"Native import failed: export={export_status}, import={imported.returncode}"
            )
    finally:
        if exporter.poll() is None:
            exporter.terminate()
            exporter.wait()
    print(
        json.dumps(
            {
                "node": node,
                "platform": platform,
                "images": images,
                "import": "completed",
            }
        )
    )


if __name__ == "__main__":
    main()
