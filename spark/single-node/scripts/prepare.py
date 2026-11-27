"""Materialize repository-owned source links as ordinary Docker-context files."""
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def main():
    inventory = {}
    for name, link in (("orders.py", "orders-workload/orders.py"), ("verify.py", "parquet-verifier/verify.py")):
        source = (ROOT / link).resolve(strict=True)
        if not source.is_relative_to(REPO):
            raise ValueError(f"Source must belong to this repository: {source}")
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
