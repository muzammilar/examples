"""Import only required native images onto the example's role-assigned kind nodes."""

import json
import shutil
import subprocess

cluster = "starrocks-operator"
nodes = subprocess.check_output(
    ["kind", "get", "nodes", "--name", cluster], text=True
).split()
if len(nodes) != 4:
    raise RuntimeError("expected four owned kind nodes")
assignments = {
    node: (
        ["starrocks/operator:v1.11.8", "starrocks-operator-workload:dev"]
        if node.endswith("control-plane")
        else ["starrocks/fe-ubuntu:4.1.7", "starrocks/be-ubuntu:4.1.7"]
    )
    for node in nodes
}
required = 0
for node, images in assignments.items():
    existing = set(
        subprocess.check_output(
            ["docker", "exec", node, "ctr", "-n", "k8s.io", "images", "ls", "-q"],
            text=True,
        ).splitlines()
    )
    for image in set(images):
        reference = "docker.io/" + (image if "/" in image else "library/" + image)
        if reference in existing and not image.endswith(":dev"):
            continue
        required += int(
            subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Size}}", image],
                text=True,
            )
        )
free = (
    int(
        subprocess.check_output(
            ["docker", "exec", nodes[0], "df", "-Pk", "/var/lib/containerd"], text=True
        )
        .splitlines()[-1]
        .split()[3]
    )
    * 1024
)
if free < required + 8 * 1024**3:
    raise RuntimeError(
        f"insufficient Docker disk: {free / 1024**3:.1f} GiB free; "
        f"need {required / 1024**3:.1f} GiB for missing images plus 8 GiB reserve"
    )
for node in nodes:
    info = json.loads(subprocess.check_output(["docker", "inspect", node], text=True))[
        0
    ]
    if info["Config"]["Labels"].get("io.x-k8s.kind.cluster") != cluster:
        raise RuntimeError("foreign node")
    images = assignments[node]
    for image in images:
        if shutil.disk_usage(".").free < 5 * 1024**3:
            raise RuntimeError(
                "host disk has less than 5 GiB free; preserve setup and restore capacity"
            )
        arch = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", image],
            text=True,
        ).strip()
        if arch != "arm64":
            raise RuntimeError("this local example expects native arm64 images")
        exported = subprocess.Popen(
            ["docker", "save", "--platform", "linux/" + arch, image],
            stdout=subprocess.PIPE,
        )
        imported = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                node,
                "ctr",
                "-n",
                "k8s.io",
                "images",
                "import",
                "--digests",
                "-",
            ],
            stdin=exported.stdout,
            check=False,
        )
        exported.stdout.close()
        if exported.wait() or imported.returncode:
            raise RuntimeError("native image import failed")
