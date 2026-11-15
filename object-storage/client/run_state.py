"""Retain old evidence when starting against a verified empty example namespace."""

from pathlib import Path
import uuid

OWNED = {
    "manifest",
    "cases-manifest",
    "benchmark-manifest",
    "benchmark",
    "benchmark-failures",
    "seed",
    "correctness",
    "fault-history",
    "cleanup",
    "physical-baseline",
    "physical-recovery",
    "replica-byte-audit",
    "metadata-audit",
    "volume-placement",
    "resource-limits",
    "heal-start",
    "heal-status",
    "native-audit",
}


def archive_oracles(root, extra=()):
    root = Path(root)
    entries = [
        p
        for p in root.iterdir()
        if (p.is_file() and p.suffix == ".json" and p.stem in OWNED | set(extra))
        or (p.is_dir() and p.name.startswith("fault-"))
    ]
    if not entries:
        return
    archive = root / "previous-runs" / uuid.uuid4().hex
    archive.mkdir(parents=True)
    for p in entries:
        p.rename(archive / p.name)
