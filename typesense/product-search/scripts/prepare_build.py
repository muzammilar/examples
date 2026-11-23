"""Materialize tracked corpus/oracle source into local, ignored build contexts."""

import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def materialize(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def prepare():
    corpus = ROOT / "retrieval-corpus"
    contexts = ROOT / ".build"
    for name in ["corpus", "tools"]:
        target = contexts / name
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
    for name in ["Dockerfile", "manifest.json"]:
        materialize(corpus / name, contexts / "corpus" / name)
    for source in (corpus / "app").iterdir():
        if source.suffix == ".py" or source.name == "requirements.txt":
            materialize(source, contexts / "corpus/app" / source.name)
    for source in (corpus / "client").iterdir():
        if source.suffix == ".py" or source.name == "requirements.txt":
            materialize(source, contexts / "tools" / source.name)
    for source in (ROOT / "app").glob("*.py"):
        target = contexts / "tools" / source.name
        target.unlink(missing_ok=True)
        shutil.copy2(source, target)
    for name in ["version_history.py", "replica_audit.py", "search_checks.py"]:
        shutil.copy2(ROOT / ("tests" if name.startswith("test_") else "scripts") / name, contexts / "tools" / name)
    shutil.copy2(ROOT / "app/Dockerfile", contexts / "tools/Dockerfile")


if __name__ == "__main__":
    prepare()
