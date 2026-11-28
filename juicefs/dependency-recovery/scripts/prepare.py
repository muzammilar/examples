"""Materialize only tracked sources into the ignored local Docker context."""

import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "file-publication/posix-workload-source"


def prepare():
    destination = ROOT / ".build/docker"
    destination.mkdir(parents=True, exist_ok=True)
    receipt = {}
    paths = [
        (SOURCE / name, destination / "posix-source" / name)
        for name in ("main.cpp", "CMakeLists.txt")
    ]
    paths += [
        (ROOT / "file-publication" / name, destination / name)
        for name in ("Dockerfile", "mount.sh")
    ]
    for src, dst in paths:
        real = src.resolve(strict=True)
        real.relative_to(ROOT.parents[1])
        dst.parent.mkdir(parents=True, exist_ok=True)
        receipt[str(dst.relative_to(ROOT))] = hashlib.sha256(
            real.read_bytes()
        ).hexdigest()
        if dst.exists():
            if (
                hashlib.sha256(dst.read_bytes()).digest()
                == hashlib.sha256(real.read_bytes()).digest()
            ):
                continue
            dst.unlink()
        try:
            os.link(real, dst)
        except OSError:
            shutil.copyfile(real, dst)
        receipt[str(dst.relative_to(ROOT))] = hashlib.sha256(
            dst.read_bytes()
        ).hexdigest()
    (ROOT / ".build/materialization.json").write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
    return destination


if __name__ == "__main__":
    print(prepare())
