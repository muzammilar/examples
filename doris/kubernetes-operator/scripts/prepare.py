"""Prepare repository-owned source and checksum-pinned upstream manifests offline."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]


def main():
    configuration = json.loads((ROOT / "config/upstream.json").read_text())
    results = ROOT / "results"
    results.mkdir(exist_ok=True)
    archive = results / "operator-26.0.1.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(
            configuration["source_url"], timeout=60
        ) as response:
            archive.write_bytes(response.read())
    if (
        hashlib.sha256(archive.read_bytes()).hexdigest()
        != configuration["source_sha256"]
    ):
        raise ValueError(
            "Upstream source checksum mismatch; refusing to render/install"
        )
    build = ROOT / ".build"
    operator = build / "operator"
    operator.mkdir(parents=True, exist_ok=True)
    wanted = {
        "config/operator/operator-sign-ns.yaml": "operator.yaml",
        "config/crd/bases/doris.selectdb.com_dorisclusters.yaml": "doris.selectdb.com.yaml",
    }
    # Select exact files rather than extracting arbitrary archive paths.
    with tarfile.open(archive) as source:
        for path, target in wanted.items():
            member = source.extractfile("doris-operator-26.0.1/" + path)
            if member is None:
                raise ValueError("Missing required upstream manifest: " + path)
            (operator / target).write_bytes(member.read())
    shutil.copyfile(
        ROOT / "deploy/operator-kustomization.yaml", operator / "kustomization.yaml"
    )
    shutil.copyfile(
        ROOT / "deploy/operator-patch.json", operator / "operator-patch.json"
    )
    inventory = {}
    for name in ("main.go", "go.mod", "go.sum"):
        source = (ROOT / "orders-workload" / name).resolve(strict=True)
        if not source.is_relative_to(REPO):
            raise ValueError("Workload source is outside repository")
        target = build / "orders-workload" / name
        target.parent.mkdir(exist_ok=True)
        target.unlink(missing_ok=True)
        try:
            os.link(source, target)
            method = "hardlink"
        except OSError:
            shutil.copyfile(source, target)
            method = "copy"
        inventory[name] = {
            "source": str(source.relative_to(REPO)),
            "method": method,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
    (build / "source-inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    binary = ROOT / "results/tools/kubectl"
    manifest = subprocess.check_output(
        [str(binary), "kustomize", str(operator)], text=True
    )
    (results / "operator-rendered.yaml").write_text(manifest)


if __name__ == "__main__":
    main()
