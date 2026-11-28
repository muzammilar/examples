"""Materialize only tracked repository source into the ignored Docker context."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
source = (ROOT / "incident-workload").resolve(strict=True)
if source != ROOT.parent / "incident-workload":
    raise RuntimeError("Unexpected comparison workload source")
context = ROOT / ".build/incident-workload"
context.mkdir(parents=True, exist_ok=True)
files = [source / name for name in ["Dockerfile", "go.mod", "go.sum", ".dockerignore"]]
files.extend(source.glob("*.go"))
for old in context.iterdir():
    if not old.is_file():
        raise RuntimeError("Unexpected generated-context directory")
    old.unlink()
manifest = []
for original in sorted(files):
    target = original.resolve(strict=True)
    relative = target.relative_to(REPO)
    if original.is_symlink():
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(relative)],
            cwd=REPO,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    destination = context / original.name
    try:
        os.link(target, destination)
    except OSError:
        shutil.copy2(target, destination)
    manifest.append(
        hashlib.sha256(target.read_bytes()).hexdigest() + "  " + original.name
    )
(context / "source.sha256").write_text("\n".join(manifest) + "\n")
