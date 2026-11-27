"""Install the checksum-pinned kubectl inside this example; never modify host tools."""

import hashlib
import json
from pathlib import Path
import platform
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT / "config/kubectl.json").read_text())
    machine = platform.machine().lower()
    arch = {
        "aarch64": "arm64",
        "arm64": "arm64",
        "x86_64": "amd64",
        "amd64": "amd64",
    }.get(machine)
    system = platform.system().lower()
    key = f"{system}/{arch}"
    expected = config["sha256"].get(key)
    if expected is None:
        raise SystemExit(f"Unsupported tool platform: {key}")
    binary = ROOT / "results/tools/kubectl"
    if binary.exists() and hashlib.sha256(binary.read_bytes()).hexdigest() == expected:
        return
    binary.parent.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_suffix(".download")
    url = f'https://dl.k8s.io/release/{config["version"]}/bin/{system}/{arch}/kubectl'
    try:
        with urllib.request.urlopen(url, timeout=120) as source, temporary.open(
            "wb"
        ) as target:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                target.write(chunk)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != expected:
            raise ValueError("Downloaded kubectl failed its pinned checksum")
        temporary.chmod(0o755)
        temporary.replace(binary)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
