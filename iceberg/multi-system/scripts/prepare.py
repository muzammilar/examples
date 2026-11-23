"""Materialize tracked workload links into example-owned Docker contexts."""

import errno
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
SOURCES = ("clickhouse-archive-workload", "iceberg-compaction")


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in (errno.EXDEV, errno.EPERM, errno.ENOTSUP):
            raise
        shutil.copy2(source, destination)


def main():
    build = ROOT / ".build"
    if build.is_symlink():
        raise RuntimeError("refuse a symlinked build directory")
    build.mkdir(exist_ok=True)
    for name in SOURCES:
        source = (ROOT / "workloads" / name).resolve(strict=True)
        if not source.is_dir() or not source.is_relative_to(REPOSITORY):
            raise RuntimeError("source must be a directory inside this checkout")
        paths = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(REPOSITORY),
                    "ls-files",
                    "-z",
                    "--",
                    str(source.relative_to(REPOSITORY)),
                ]
            )
            .decode()
            .split("\0")
        )
        files = [REPOSITORY / path for path in paths if path]
        if not files or any(path.is_symlink() or not path.is_file() for path in files):
            raise RuntimeError("source must contain regular repository-tracked files")
        target = build / name
        if target.is_symlink():
            raise RuntimeError("refuse a symlinked materialization destination")
        with tempfile.TemporaryDirectory(prefix=name + "-", dir=build) as staging:
            temporary = Path(staging) / name
            temporary.mkdir()
            for path in files:
                destination = temporary / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                link_or_copy(path, destination)
            if target.exists():
                shutil.rmtree(target)
            temporary.rename(target)
        print(f"{name}: {len(files)} tracked files materialized")


if __name__ == "__main__":
    main()
