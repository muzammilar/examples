"""Pin downloads in the ignored local build context; never depend on sibling outputs."""

import hashlib
import json
from pathlib import Path
import shutil
import urllib.request

ROOT = Path(__file__).resolve().parent
RAY = "38fd4be3560d63a5514c1e6883685fe496d45e1a"
DUCK = "2.0.0"


def main():
    out = ROOT / ".build"
    out.mkdir(exist_ok=True)
    artifacts = {
        "rayforce.tar.gz": f"https://codeload.github.com/RayforceDB/rayforce/tar.gz/{RAY}",
        "duckdb-arm64.tar.gz": f"https://github.com/duckdb/duckdb/releases/download/v{DUCK}/duckdb-shared-libs-linux-arm64.tar.gz",
        "duckdb-amd64.tar.gz": f"https://github.com/duckdb/duckdb/releases/download/v{DUCK}/duckdb-shared-libs-linux-amd64.tar.gz",
    }
    pins = json.loads((ROOT / "sources.json").read_text())
    for name, url in artifacts.items():
        path = out / name
        if not path.exists():
            temporary = path.with_suffix(".partial")
            urllib.request.urlretrieve(url, temporary)
            temporary.replace(path)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != pins[name]["sha256"] or url != pins[name]["url"]:
            raise RuntimeError("source checksum mismatch: " + name)
    workload = out / "analytics-workload"
    if workload.exists():
        shutil.rmtree(workload)
    shutil.copytree(
        ROOT / "analytics-workload",
        out / "analytics-workload",
        ignore=shutil.ignore_patterns("target", "__pycache__"),
    )
    shutil.copyfile(ROOT / "docker/Dockerfile", out / "Dockerfile")


if __name__ == "__main__":
    main()
