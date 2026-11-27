"""Materialize tracked VM launch source into this example's own build context."""

from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
target = root / ".build/runtime"
target.mkdir(parents=True, exist_ok=True)
for name in ("boot.py", "capacity.py"):
    source = root / "vm-runtime" / name
    if not source.resolve().is_relative_to(root.parents[1]):
        raise ValueError("VM source is outside the repository")
    shutil.copy2(source, target / name)
