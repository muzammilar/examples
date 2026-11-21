"""Measure one native AOF application in the example-owned Linux volume."""

import json
import os
import pathlib
import resource
import subprocess
import time

inputs = pathlib.Path("/data")
if (
    sorted(p.name for p in inputs.iterdir()) != [".isolation-token"]
    or (inputs / ".isolation-token").read_text() != os.environ["JOURNAL_INPUT_TOKEN"]
):
    raise RuntimeError("original input isolation was not proved")
command = [
    "/opt/bench/rayforce_aof",
    os.environ["AOF_MODE"],
    "/state",
    os.environ["AOF_ROWS"],
    os.environ["AOF_BYTES"],
    os.environ["AOF_SEGMENT_LIMIT"],
]
started = time.monotonic_ns()
code = subprocess.run(command).returncode
exit_code = code if code >= 0 else 128 - code
usage = resource.getrusage(resource.RUSAGE_CHILDREN)
# Same measurement record as the native CLI harness; no request journal is used.
print(
    "__JOURNAL_JOB__"
    + json.dumps(
        {
            "exit_code": exit_code,
            "child_returncode": code,
            "monotonic_resolution_seconds": time.get_clock_info("monotonic").resolution,
            "command": command,
            "wall_ns": time.monotonic_ns() - started,
            "process_cpu_core_seconds": usage.ru_utime + usage.ru_stime,
            "process_peak_rss_bytes": usage.ru_maxrss * 1024,
            "mountinfo": pathlib.Path("/proc/self/mountinfo").read_text(),
            "cgroup": {
                name: pathlib.Path("/sys/fs/cgroup", name).read_text()
                for name in ("cpu.stat", "memory.peak", "memory.events", "io.stat")
            },
        }
    ),
    flush=True,
)
raise SystemExit(exit_code)
