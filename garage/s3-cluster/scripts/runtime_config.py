"""A bind-mounted TOML change needs an actual rolling process restart."""

import hashlib
import os
from pathlib import Path
import subprocess

root = Path(os.environ.get("GARAGE_CONFIG_DIR", "config/generated"))
files = sorted(root.glob("garage*.toml"))
if len(files) != 9:
    raise RuntimeError("expected all nine native TOML files")
digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
marker = root / "runtime-config.sha256"
if marker.exists() and marker.read_text().strip() == digest:
    raise SystemExit(0)
for i in range(9):
    subprocess.run(["docker", "compose", "restart", f"garage{i}"], check=True)
    subprocess.run(
        ["docker", "compose", "up", "-d", "--wait", f"garage{i}"], check=True
    )
marker.write_text(digest + "\n")
