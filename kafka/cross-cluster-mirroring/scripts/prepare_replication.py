"""Materialize tracked producer source and the local observer into an ignored build context."""

import errno
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def link_or_copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError as error:
        if error.errno not in {errno.EXDEV, errno.EPERM, errno.ENOTSUP}:
            raise
        shutil.copy2(source, destination)


def main():
    build = ROOT / ".build"
    output = build / "replication"
    if build.is_symlink() or output.is_symlink():
        raise RuntimeError("refusing a symlinked materialization destination")
    source = (ROOT / "replication-producer").resolve(strict=True)
    if not source.is_relative_to(REPO):
        raise RuntimeError("producer source is outside this repository")
    tracked = (
        subprocess.run(
            [
                "git",
                "-C",
                str(REPO),
                "ls-files",
                "-z",
                "--",
                str(source.relative_to(REPO)),
            ],
            check=True,
            capture_output=True,
        )
        .stdout.decode()
        .split("\0")
    )
    producer = [REPO / name for name in tracked if name]
    if not producer or any(
        path.is_symlink() or not path.is_file() for path in producer
    ):
        raise RuntimeError("producer must contain regular repository-tracked files")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    for path in producer:
        link_or_copy(path, output / "producer" / path.relative_to(source))
    observer = ROOT / "replication-observer"
    paths = [observer / name for name in ("Cargo.toml", "Cargo.lock", "build.rs")]
    paths.extend((observer / "src").glob("*.rs"))
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("observer source must be a regular local file")
        link_or_copy(path, output / "consumer" / path.relative_to(observer))
    print(
        json.dumps(
            {
                "producer_source": str(source.relative_to(REPO)),
                "producer_files": len(producer),
                "observer_files": len(paths),
                "context": str(output),
            }
        )
    )


if __name__ == "__main__":
    main()
