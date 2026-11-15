"""Import the pinned native images into only this example's kind nodes."""

import json
import shutil
import subprocess

with open("images.json") as manifest:
    images = json.load(manifest)
assignments = {
    "neon-storage-control-plane": {
        images["broker"],
        images["pageserver"],
        images["sk0"],
        images["compute"],
        images["runner"],
    },
    "neon-storage-worker": {images["sk0"]},
    "neon-storage-worker2": {images["sk0"], images["compute"]},
}
nodes = subprocess.check_output(
    ["kind", "get", "nodes", "--name", "neon-storage"], text=True
).split()
if set(nodes) != set(assignments):
    raise RuntimeError("expected this example's three kind nodes")
# All kind nodes share Docker Desktop's backing disk. Reserve capacity for the
# complete import plan rather than checking each node in isolation.
required = 0
for node in nodes:
    existing = set(
        subprocess.check_output(
            ["docker", "exec", node, "ctr", "-n", "k8s.io", "images", "ls", "-q"],
            text=True,
        ).splitlines()
    )
    for image in assignments[node]:
        reference = image if "/" in image else "docker.io/library/" + image
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
    value = json.loads(subprocess.check_output(["docker", "inspect", node], text=True))[
        0
    ]
    if value["Config"]["Labels"].get("io.x-k8s.kind.cluster") != "neon-storage":
        raise RuntimeError("foreign kind node")
    # Match config.py's fixed role placement. Importing the workload and compute
    # images onto unused workers needlessly duplicates their unpacked layers.
    for image in sorted(assignments[node]):
        if shutil.disk_usage(".").free < 5 * 1024**3:
            raise RuntimeError(
                "host disk has less than 5 GiB free; preserve setup and restore capacity"
            )
        arch = subprocess.check_output(
            ["docker", "image", "inspect", "--format", "{{.Architecture}}", image],
            text=True,
        ).strip()
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
                "--namespace",
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
        if exported.wait() != 0 or imported.returncode != 0:
            raise RuntimeError("native image import failed")
