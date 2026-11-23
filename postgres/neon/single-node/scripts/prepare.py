"""Materialize tracked source links as local regular files for Docker contexts."""

import errno
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

root = Path.cwd()
repository = Path(
    subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
).resolve()
staging = root / ".build"
staging.mkdir(exist_ok=True)


def materialize_file(source, destination):
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP):
            raise
        shutil.copy2(source, destination)
    return destination


for name in sys.argv[1:]:
    if Path(name).name != name:
        raise ValueError("expected a local source-link name")
    source = (root / name).resolve(strict=True)
    if not source.is_relative_to(repository) or not source.is_dir():
        raise ValueError("source must be a directory inside this checkout")
    for linked in source.rglob("*"):
        if linked.is_symlink() and not linked.resolve(strict=True).is_relative_to(
            repository
        ):
            raise ValueError("nested source link points outside this checkout")
    temporary = Path(tempfile.mkdtemp(prefix=name + "-", dir=staging))
    try:
        shutil.copytree(
            source,
            temporary / name,
            symlinks=False,
            copy_function=materialize_file,
            ignore=shutil.ignore_patterns("__pycache__", ".build", "results"),
        )
        target = staging / name
        if target.exists():
            shutil.rmtree(target)
        (temporary / name).rename(target)
    finally:
        shutil.rmtree(temporary)
