"""Materialize repository-tracked S3 helpers into this example's Docker context."""

import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / "s3-workload"
target = root / ".build/tools"
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
for path in source.iterdir():
    if path.suffix != ".py" and path.name != "requirements.txt":
        continue
    try:
        os.link(path, target / path.name)
    except OSError:
        shutil.copy2(path, target / path.name)
for name in [
    "traffic.py",
    "storage_usage.py",
    "test_traffic.py",
    "test_reader.py",
    "witness.py",
    "placement.py",
    "replica_audit.py",
    "test_placement.py",
    "audit-requirements.txt",
]:
    (target / name).unlink(missing_ok=True)
    source_path = root / name
    if name.endswith(".py"):
        source_path = root / ("tests" if name.startswith("test_") else "scripts") / name
    shutil.copy2(source_path, target / name)
shutil.copy2(root / "docker/Dockerfile.tools", target / "Dockerfile")
print("Prepared", target)
