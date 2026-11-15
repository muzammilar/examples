"""Bootstrap only this example's nine native Garage peers and S3 namespace."""

import json
import os
from pathlib import Path
import subprocess


def cli(node, *args, capture=False):
    return subprocess.run(
        ["docker", "compose", "exec", "-T", f"garage{node}", "/garage", *args],
        check=True,
        text=True,
        capture_output=capture,
    ).stdout


peers = [cli(i, "node", "id", "-q", capture=True).strip() for i in range(9)]
marker = (
    Path(os.environ.get("GARAGE_CONFIG_DIR", "config/generated")) / "bootstrap.json"
)
if marker.exists():
    if [p.split("@")[0] for p in json.loads(marker.read_text())["peers"]] != [
        p.split("@")[0] for p in peers
    ]:
        raise RuntimeError("node identities changed; use the complete clean lifecycle")
else:
    for peer in peers[1:]:
        cli(0, "node", "connect", peer)
    for i, peer in enumerate(peers):
        cli(
            0,
            "layout",
            "assign",
            "--zone",
            f"zone{i // 3}",
            "--capacity",
            "4G",
            peer.split("@")[0],
        )
    cli(0, "layout", "apply", "--version", "1")
    key = os.environ["S3_ACCESS_KEY"]
    cli(
        0,
        "key",
        "import",
        "--yes",
        key,
        os.environ["S3_SECRET_KEY"],
        "-n",
        "lab-client",
    )
    cli(0, "key", "allow", key, "--create-bucket")
    cli(0, "bucket", "create", "lab-objects")
    cli(
        0,
        "bucket",
        "allow",
        "--read",
        "--write",
        "--owner",
        "--key",
        key,
        "lab-objects",
    )
    marker.write_text(json.dumps({"peers": peers}, indent=2) + "\n")
cli(0, "status")
