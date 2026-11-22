"""Independent immutable-file reads and timed native actions inside an owned guest."""

import hashlib
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time


def emit(value):
    print(json.dumps(value), flush=True)


def inspect_file(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"bytes": path.stat().st_size, "sha256": digest}


def read_loop(path, expected, control):
    while not control.with_suffix(".stop").exists():
        started = time.time_ns()
        phase = control.with_suffix(".phase").read_text().strip()
        cpu = time.process_time_ns()
        started_monotonic = time.monotonic_ns()
        try:
            actual = inspect_file(path)
            result = "ok" if actual == expected else "mismatch"
            error = None
        except OSError as exception:
            actual, result, error = None, "fail", str(exception)
        ended_monotonic = time.monotonic_ns()
        emit(
            {
                "kind": "read",
                "started_ns": started,
                "ended_ns": time.time_ns(),
                "elapsed_ns": ended_monotonic - started_monotonic,
                "started_monotonic_ns": started_monotonic,
                "ended_monotonic_ns": ended_monotonic,
                "cpu_ns": time.process_time_ns() - cpu,
                "phase_start": phase,
                "phase_end": control.with_suffix(".phase").read_text().strip(),
                "result": result,
                "actual": actual,
                "error": error,
                "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                * 1024,
            }
        )
        time.sleep(0.2)
    emit({"kind": "reader-stopped", "ended_ns": time.time_ns(), "pid": os.getpid()})


def main():
    if sys.argv[1] == "read":
        read_loop(Path(sys.argv[2]), json.loads(sys.argv[3]), Path(sys.argv[4]))
    elif sys.argv[1] == "action":
        started = time.time_ns()
        started_monotonic = time.monotonic_ns()
        value = subprocess.run(
            sys.argv[2:], capture_output=True, text=True, check=False
        )
        emit(
            {
                "started_ns": started,
                "ended_ns": time.time_ns(),
                "started_monotonic_ns": started_monotonic,
                "ended_monotonic_ns": time.monotonic_ns(),
                "command": sys.argv[2:],
                "exit_code": value.returncode,
                "stdout": value.stdout,
                "stderr": value.stderr,
            }
        )
    else:
        raise ValueError("unknown guest observation mode")


if __name__ == "__main__":
    main()
