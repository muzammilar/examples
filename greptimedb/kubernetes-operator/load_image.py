"""Import this example's built ARM64 client into its owned kind nodes."""

import subprocess
import sys

images = sys.argv[1:] or [
    "greptime-kind-support-client:dev",
    "greptime/greptimedb:v1.3.1",
]
nodes = subprocess.check_output(
    ["kind", "get", "nodes", "--name", "greptime-operator"], text=True
).splitlines()
if not nodes:
    raise RuntimeError("kind cluster absent")
for image in images:
    for node in nodes:
        export = subprocess.Popen(
            ["docker", "save", "--platform", "linux/arm64", image],
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
            stdin=export.stdout,
            check=False,
        )
        export.stdout.close()
        if export.wait() != 0 or imported.returncode != 0:
            raise RuntimeError("image import failed: " + node)
