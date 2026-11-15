"""Wait for the native retained-state frontend quorum, not only a TCP port."""

import csv
import io
import subprocess
import time
import sys

command = [
    "docker",
    "compose",
    "-f",
    "docker-compose.json",
    "exec",
    "-T",
    "sr-fe-0",
    "mysql",
    "-h",
    "127.0.0.1",
    "-P",
    "9030",
    "-u",
    "root",
    "--batch",
    "--raw",
    "-e",
    "SHOW FRONTENDS",
]
deadline = time.monotonic() + 300
while time.monotonic() < deadline:
    result = subprocess.run(command, text=True, capture_output=True, timeout=15)
    if result.returncode == 0:
        rows = list(csv.DictReader(io.StringIO(result.stdout), delimiter="\t"))
        if (
            len(rows) == 3
            and all(row["Alive"] == "true" for row in rows)
            and sum(row["Role"] == "LEADER" for row in rows) == 1
        ):
            print(result.stdout)
            break
    time.sleep(2)
else:
    raise RuntimeError(
        "native frontend quorum did not become healthy: "
        + result.stdout
        + result.stderr
    )

if len(sys.argv) > 1 and sys.argv[1] == "all":
    command[-1] = "SHOW BACKENDS"
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        result = subprocess.run(command, text=True, capture_output=True, timeout=15)
        if result.returncode == 0:
            rows = list(csv.DictReader(io.StringIO(result.stdout), delimiter="\t"))
            if len(rows) == 3 and all(row["Alive"] == "true" for row in rows):
                print(result.stdout)
                break
        time.sleep(2)
    else:
        raise RuntimeError(
            "native backends did not register: " + result.stdout + result.stderr
        )
