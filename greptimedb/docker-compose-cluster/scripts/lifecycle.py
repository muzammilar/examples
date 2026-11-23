"""Archive evidence only after successful workload cleanup and owned state removal."""

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys

project = sys.argv[1]
cluster = sys.argv[2] if len(sys.argv) == 3 else None
root = Path("results")
marker = root / "cleanup.json"
if not marker.exists():
    raise SystemExit(0)
cleanup = json.loads(marker.read_text())
if not any(
    cleanup.get(key) is True
    for key in (
        "workload_dropped",
        "workload_tables_dropped",
        "greptime_tables_dropped",
    )
):
    raise RuntimeError("cleanup marker does not prove workload removal")
for command in (
    ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=" + project],
    [
        "docker",
        "volume",
        "ls",
        "-q",
        "--filter",
        "label=com.docker.compose.project=" + project,
    ],
):
    if subprocess.check_output(command, text=True).strip():
        raise SystemExit(0)
if (
    cluster
    and cluster
    in subprocess.check_output(["kind", "get", "clusters"], text=True).split()
):
    raise SystemExit(0)
archive = root / ("archive-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%f"))
archive.mkdir()
for path in list(root.iterdir()):
    if path.name.startswith("archive-"):
        continue
    shutil.move(path, archive / path.name)
print("Previous completed lifecycle evidence preserved in", archive)
