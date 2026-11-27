"""Expose tracked workload sources through ordinary, ignored Docker-context files."""
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def main():
    sources = {
        "orders.py": "orders-workload/orders.py",
        "verify.py": "parquet-verifier/verify.py",
        "live_reader.py": "parquet-verifier/live_reader.py",
        "scripts/start.sh": "scripts/hadoop-start.sh",
        "hadoop-config/core-site.xml": "config/hadoop/core-site.xml",
        "hadoop-config/hdfs-site.xml": "config/hadoop/hdfs-site.xml",
    }
    inventory = {}
    for name, relative in sources.items():
        source = (ROOT / relative).resolve(strict=True)
        if not source.is_relative_to(REPO):
            raise ValueError(f"Non-repository source: {source}")
        target = ROOT / ".build" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        try:
            os.link(source, target)
            method = "hardlink"
        except OSError:
            shutil.copyfile(source, target)
            method = "copy"
        inventory[name] = {"source": str(source.relative_to(REPO)), "method": method,
                           "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
    (ROOT / ".build/source-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")


if __name__ == "__main__":
    main()
