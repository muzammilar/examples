"""Materialize tracked S3 source as regular files inside this example."""

import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
target = root / ".build/tools"
if target.exists():
    shutil.rmtree(target)
target.mkdir(parents=True)
for path in (root / "s3-workload").iterdir():
    if path.suffix != ".py" and path.name != "requirements.txt":
        continue
    try:
        os.link(path, target / path.name)
    except OSError:
        shutil.copy2(path, target / path.name)
for name in [
    "traffic.py",
    "witness.py",
    "test_traffic.py",
    "test_reader.py",
    "native_api.py",
    "init_storage.py",
    "physical.py",
    "native_health.py",
    "test_native.py",
    "test_physical.py",
]:
    (target / name).unlink(missing_ok=True)
    shutil.copy2(root / ("tests" if name.startswith("test_") else "scripts") / name, target / name)
shutil.copy2(root / "docker/Dockerfile.tools", target / "Dockerfile")
