"""Download immutable upstream artifacts; promote files only after size/hash checks."""

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
CACHE = Path(os.environ.get("ESCI_CACHE", ROOT / "cache"))


def verify(path, artifact):
    if not path.exists() or path.stat().st_size != artifact["bytes"]:
        return False
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest() == artifact["sha256"]


def fetch(artifact):
    CACHE.mkdir(exist_ok=True)
    path = CACHE / Path(artifact["path"]).name
    if verify(path, artifact):
        print(path.name + ": verified cached payload", flush=True)
        return
    url = (
        artifact.get("url")
        or f"https://media.githubusercontent.com/media/amazon-science/esci-data/{MANIFEST['revision']}/{artifact['path']}"
    )
    partial = path.with_suffix(".part")
    h = hashlib.sha256()
    size = 0
    with (
        urllib.request.urlopen(url, timeout=120) as response,
        partial.open("wb") as out,
    ):
        while chunk := response.read(1024 * 1024):
            out.write(chunk)
            h.update(chunk)
            size += len(chunk)
    if size != artifact["bytes"] or h.hexdigest() != artifact["sha256"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError("artifact size/hash mismatch: " + path.name)
    partial.replace(path)
    print(f"{path.name}: verified {size} bytes", flush=True)


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(fetch, MANIFEST["artifacts"]))
