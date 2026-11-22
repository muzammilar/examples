"""Pin downloads in the ignored local build context; never depend on sibling outputs."""

import hashlib
import gzip
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
    extensions = json.loads((ROOT / "quack-sources.json").read_text())
    if extensions["duckdb_version"] != DUCK:
        raise RuntimeError("Quack artifacts target a different DuckDB engine")
    for name, entry in extensions["artifacts"].items():
        artifacts[name] = entry["url"]
        pins[name] = entry
    for name, url in artifacts.items():
        path = out / name
        if not path.exists():
            temporary = path.with_suffix(".partial")
            headers = {}
            if name in extensions["artifacts"]:
                headers["User-Agent"] = f"duckdb/v{DUCK}({pins[name]['platform']})"
            request = urllib.request.Request(url, headers=headers)
            with (
                urllib.request.urlopen(request, timeout=60) as source,
                temporary.open("wb") as target,
            ):
                shutil.copyfileobj(source, target)
            temporary.replace(path)
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != pins[name]["sha256"] or url != pins[name]["url"]:
            raise RuntimeError("source checksum mismatch: " + name)
        if name in extensions["artifacts"]:
            with gzip.open(path, "rb") as source:
                unpacked = hashlib.file_digest(source, "sha256").hexdigest()
            if unpacked != pins[name]["uncompressed_sha256"]:
                raise RuntimeError("extension checksum mismatch: " + name)
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
