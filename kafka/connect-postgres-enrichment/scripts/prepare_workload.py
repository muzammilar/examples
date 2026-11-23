"""Materialize repository-owned workload sources into a local Docker context."""

import errno
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def materialize(source, destination):
    source = source.resolve(strict=True)
    if not source.is_relative_to(REPO) or not source.is_file():
        raise RuntimeError("workload source must be a regular repository file")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP}:
            raise
        shutil.copy2(source, destination)


def main():
    build = ROOT / ".build"
    output = build / "enrichment"
    if build.is_symlink() or output.is_symlink():
        raise RuntimeError("refusing symlinked build output")
    if output.exists():
        shutil.rmtree(output)
    source = (ROOT / "orders-producer").resolve(strict=True)
    if not source.is_relative_to(REPO):
        raise RuntimeError("producer source is outside the repository")
    names = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files", "-z", "--", str(source)],
        text=True,
    ).split("\0")
    if not any(names):
        raise RuntimeError("producer source is not tracked")
    for name in filter(None, names):
        path = REPO / name
        materialize(path, output / "producer" / path.relative_to(source))
    observer = ROOT / "enrichment-observer"
    for path in [observer / n for n in ("Cargo.toml", "Cargo.lock", "build.rs")]:
        materialize(path, output / "consumer" / path.relative_to(observer))
    for path in (observer / "src").glob("*.rs"):
        materialize(path, output / "consumer/src" / path.name)
    materialize(ROOT / "proto/order.proto", output / "proto/order.proto")


if __name__ == "__main__":
    main()
