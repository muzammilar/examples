"""Materialize repository-tracked S3 helpers into this example's Docker context."""

import os
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent
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
for name in ["traffic.py", "test_traffic.py", "witness.py"]:
    shutil.copy2(root / name, target / name)
shutil.copy2(root / "docker/Dockerfile.tools", target / "Dockerfile")
print("Prepared", target)
