"""Materialize the tracked POSIX workload into this lab's ignored build context."""

import errno
import os
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[2]
source = Path(__file__).parent / "posix-workload-source"
target = root / ".build/kernel-workload"
target.mkdir(parents=True, exist_ok=True)
for name in ("main.cpp", "CMakeLists.txt"):
    path = (source / name).resolve(strict=True)
    if not path.is_relative_to(root.parents[1]):
        raise RuntimeError("Workload source is outside this repository")
    destination = target / name
    destination.unlink(missing_ok=True)
    try:
        os.link(path, destination)
    except OSError as error:
        if error.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP):
            raise
        shutil.copy2(path, destination)
