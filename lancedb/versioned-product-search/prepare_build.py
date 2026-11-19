"""Materialize tracked source as regular files in this example's ignored contexts."""

import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent


def materialize(source, target):
    source = source.resolve(strict=True)
    if not source.is_relative_to(ROOT.parents[1]) or not source.is_file():
        raise ValueError("build source must be a repository file")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def context(name):
    target = ROOT / ".build" / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def prepare_corpus():
    source = ROOT / "retrieval-corpus"
    target = context("corpus")
    for name in ["Dockerfile", "manifest.json"]:
        materialize(source / name, target / name)
    for path in (source / "app").iterdir():
        if path.suffix == ".py" or path.name == "requirements.txt":
            materialize(path, target / "app" / path.name)


def prepare():
    prepare_corpus()
    target = context("tools")
    for path in (ROOT / "retrieval-corpus/client").iterdir():
        if path.name in {"workload.py", "requirements.txt"}:
            materialize(path, target / path.name)
    for path in (ROOT / "app").glob("*.py"):
        (target / path.name).unlink(missing_ok=True)
        materialize(path, target / path.name)
    requirements = target / "requirements.txt"
    requirements.unlink()
    requirements.write_text(
        (ROOT / "retrieval-corpus/client/requirements.txt").read_text()
        + (ROOT / "app/requirements.txt").read_text()
    )
    materialize(ROOT / "app/Dockerfile", target / "Dockerfile")
    target = context("inventory-workload")
    for path in (ROOT / "inventory-workload").iterdir():
        if path.suffix in {".go", ".mod", ".sum"} or path.name == "Dockerfile":
            materialize(path, target / path.name)


if __name__ == "__main__":
    prepare()
