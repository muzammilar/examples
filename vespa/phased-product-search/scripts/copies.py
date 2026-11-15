"""Use Vespa's native replica override, rather than querying only active owners."""

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time

phase = sys.argv[1]
if phase not in ("baseline", "fault"):
    raise ValueError("phase must be baseline or fault")
root = Path(os.environ.get("CORPUS_DIR", "../../datasets/esci/prepared"))
results = Path(os.environ["RESULTS_DIR"])
results.mkdir(parents=True, exist_ok=True)
n = json.loads((root / "manifest.json").read_text())["parameters"]["documents"]
count = 1 if os.environ.get("MODE") == "single" else 3
compose = ["docker", "compose", "--profile", "cluster"]
fields = (
    "pid,source_id,locale,name,description,brand,color,tenant,stock,price_cents,version"
)
ids = results / (".native-" + phase + "-ids.txt")
ids.write_text("".join(f"id:catalog:product::{i}\n" for i in range(n)))


def export(node):
    path = results / f".native-{phase}-{node}.json"
    started = time.monotonic()
    with ids.open() as source, path.open("w") as target:
        subprocess.run(
            compose
            + [
                "exec",
                "-T",
                "-e",
                "LANG=C.UTF-8",
                "-e",
                "LC_ALL=C.UTF-8",
                "-e",
                # The image default can render Unicode as ? through Java PrintStream.
                "JAVA_TOOL_OPTIONS=-Dfile.encoding=UTF-8 -Dsun.stdout.encoding=UTF-8 -Dstdout.encoding=UTF-8",
                f"node{node}",
                "vespa-get",
                "--cluster",
                "catalog",
                "--replica-override",
                str(node),
                "--fieldset",
                "product:" + fields,
                "--timeout",
                "120",
            ],
            stdin=source,
            stdout=target,
            check=True,
            timeout=600,
        )
    print(
        f"Exported node {node}: {n} requested IDs in {time.monotonic() - started:.2f}s",
        flush=True,
    )
    return node, path.name


try:
    with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
        exports = list(pool.map(export, range(count)))
    for node, filename in exports:
        subprocess.run(
            compose
            + [
                "run",
                "--rm",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
                "-e",
                "NATIVE_FILE=" + filename,
                "-e",
                "NATIVE_PHASE=" + phase,
                "-e",
                "NATIVE_NODE=" + str(node),
                "tools",
                "verify-copy",
            ],
            check=True,
        )
finally:
    ids.unlink(missing_ok=True)
