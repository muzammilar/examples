"""Materialize tracked comparison source; generated Docker context is never committed."""

import hashlib
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "telemetry-workload").resolve(strict=True)
if source != ROOT.parent / "telemetry-workload":
    raise RuntimeError("Unexpected workload symlink target")
context = ROOT / ".build/telemetry-workload"
context.mkdir(parents=True, exist_ok=True)
names = {"Dockerfile", "go.mod", "go.sum", ".dockerignore"}
names.update(p.name for pattern in ["*.go", "*.yaml"] for p in source.glob(pattern))
if not all((source / name).is_file() for name in names):
    raise RuntimeError("Incomplete source/dependency lockfiles")
for old in context.iterdir():
    if old.is_file():
        old.unlink()
    else:
        raise RuntimeError("Unexpected directory inside the generated context")
manifest = []
for name in sorted(names):
    original, target = source / name, context / name
    try:
        os.link(original, target)
    except OSError:
        shutil.copy2(original, target)
    manifest.append(hashlib.sha256(original.read_bytes()).hexdigest() + "  " + name)
(context / "source.sha256").write_text("\n".join(manifest) + "\n")
