"""Verify an HDFS body with a deterministic standard-library oracle."""
import hashlib
import json
from pathlib import Path
import random
import subprocess
import tempfile
import sys


with tempfile.TemporaryDirectory() as directory:
    source = Path(directory) / "body.bin"
    rng = random.Random(42193)
    digest = hashlib.sha256()
    with source.open("wb") as stream:
        for _ in range(1024):
            block = rng.randbytes(65536)
            stream.write(block)
            digest.update(block)
    if len(sys.argv) == 1:
        subprocess.run(["hdfs", "dfs", "-put", str(source), "/lab/body.bin"], check=True)
    elif sys.argv[1:] != ["verify"]:
        raise ValueError("Expected no argument or verify")
    actual = hashlib.sha256()
    count = 0
    child = subprocess.Popen(["hdfs", "dfs", "-cat", "/lab/body.bin"], stdout=subprocess.PIPE)
    try:
        for block in iter(lambda: child.stdout.read(65536), b""):
            actual.update(block)
            count += len(block)
    finally:
        child.stdout.close()
        status = child.wait()
    if status or count != 67108864 or actual.digest() != digest.digest():
        raise ValueError("HDFS complete-body verification failed")
    print(json.dumps({"bytes": count, "sha256": actual.hexdigest(), "wrong": 0}))
