"""Prepare this example's verified ESCI inputs before starting database servers."""

import hashlib
import json
import os
from pathlib import Path
import subprocess

from prepare_build import prepare_corpus

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "results/corpus"
PROJECT = "lancedb-versioned-product-search"


def verified():
    path = DATA / "prepared/manifest.json"
    if not path.exists():
        return False
    manifest = json.loads(path.read_text())
    parameters = manifest["parameters"]
    expected = {
        "documents": int(os.environ.get("DOCUMENTS", "100000")),
        "queries": int(os.environ.get("QUERIES", "1000")),
        "seed": int(os.environ.get("SEED", "20261115")),
        "model": "Qdrant/bge-small-en-v1.5-onnx-Q",
        "revision": "aa8f8b060edb00e03bfdd08813a2949946c8ba55",
        "dimension": 384,
        "max_tokens": 128,
        "description_chars": 4000,
        "schema_version": 2,
    }
    if parameters != expected:
        raise RuntimeError(
            "existing corpus parameters differ; preserve it and select another owned output directory"
        )
    required = {
        "products.jsonl",
        "queries.jsonl",
        "vectors.f32",
        "query_vectors.f32",
        "exact.json",
    }
    if not required.issubset(manifest["sha256"]):
        raise RuntimeError("incomplete corpus fingerprint")
    for name, expected_hash in manifest["sha256"].items():
        path = DATA / "prepared" / name
        if path.parent != DATA / "prepared":
            raise RuntimeError("invalid corpus artifact name")
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected_hash:
                raise RuntimeError("corpus checksum mismatch: " + name)
    return True


def main():
    os.chdir(ROOT)
    prepare_corpus()
    if verified():
        print("Local corpus parameters and all artifact hashes verified", flush=True)
        return
    active = subprocess.check_output(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.oneoff=False",
        ],
        text=True,
    ).strip()
    if active:
        raise RuntimeError(
            "prepare the corpus before starting this example's database servers"
        )
    DATA.mkdir(parents=True, exist_ok=True)
    lock = DATA / "preparation.lock"
    with lock.open("x"):
        try:
            for name in ["cache", "prepared"]:
                (DATA / name).mkdir(exist_ok=True)
            subprocess.run(
                ["python3", ".build/corpus/app/download.py"],
                env=os.environ | {"ESCI_CACHE": str(DATA / "cache")},
                check=True,
            )
            compose = [
                "docker",
                "compose",
                "--file",
                str(ROOT / "docker-compose.yml"),
                "--project-name",
                PROJECT,
            ]
            subprocess.run(compose + ["build", "corpus"], check=True)
            subprocess.run(
                compose
                + ["run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}", "corpus"],
                check=True,
            )
            if not verified():
                raise RuntimeError("corpus preparation did not finish")
        finally:
            lock.unlink()


if __name__ == "__main__":
    main()
