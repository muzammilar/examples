"""Give digest-pinned upstream images stable names for containerd imports."""

import json
import subprocess
import sys

with open("images.json") as file:
    images = json.load(file)
clean = sys.argv[1:] == ["clean"]
if sys.argv[1:] and not clean:
    raise ValueError("expected no arguments or clean")
for source, target in (("source-storage", "broker"), ("source-compute", "compute")):
    if clean:
        present = (
            subprocess.run(
                ["docker", "image", "inspect", images[target]], capture_output=True
            ).returncode
            == 0
        )
        if present:
            subprocess.run(["docker", "image", "rm", images[target]], check=True)
    else:
        subprocess.run(["docker", "pull", images[source]], check=True)
        subprocess.run(["docker", "tag", images[source], images[target]], check=True)
