"""Materialize tracked S3 source inside this example's local build context."""

import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
context = root / ".build/tools"
if context.exists():
    shutil.rmtree(context)
context.mkdir(parents=True)
for source in (root / "s3-workload").iterdir():
    if source.suffix != ".py" and source.name != "requirements.txt":
        continue
    try:
        os.link(source, context / source.name)
    except OSError:
        shutil.copy2(source, context / source.name)
for name in [
    "traffic.py",
    "witness.py",
    "test_traffic.py",
    "test_reader.py",
    "prefix_readback.py",
]:
    (context / name).unlink(missing_ok=True)
    shutil.copy2(root / ("tests" if name.startswith("test_") else "scripts") / name, context / name)
shutil.copy2(root / "docker/Dockerfile.tools", context / "Dockerfile")
