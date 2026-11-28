"""Persist whole-tool cgroup counters before a one-shot native process is removed."""

import json
from pathlib import Path
import subprocess
import sys

try:
    status = subprocess.run(sys.argv[1:], timeout=180).returncode
finally:
    print(
        json.dumps(
            dict(
                tool_counters={
                    name: (Path("/sys/fs/cgroup") / name).read_text()
                    for name in (
                        "cpu.stat",
                        "memory.current",
                        "memory.peak",
                        "memory.events",
                        "io.stat",
                    )
                }
            )
        ),
        flush=True,
    )
raise SystemExit(status)
