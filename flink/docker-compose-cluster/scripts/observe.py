"""Run a lifecycle target with timestamped container resource samples."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    results = Path("results")
    results.mkdir(exist_ok=True)
    command = ["make", *sys.argv[1:]]
    started = time.monotonic()
    with (results / "resources.jsonl").open("a") as log:
        child = subprocess.Popen(command)
        try:
            while child.poll() is None:
                containers = subprocess.check_output(
                    ["docker", "ps", "--filter",
                     "label=com.docker.compose.project=flink-state-recovery",
                     "--format", "{{.ID}}"], text=True,
                ).split()
                if not containers:
                    time.sleep(1)
                    continue
                sample = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}",
                     *containers],
                    capture_output=True, text=True, timeout=20,
                )
                native = {}
                for container in containers:
                    counters = subprocess.run(
                        ["docker", "exec", container, "sh", "-c",
                         "cat /sys/fs/cgroup/cpu.stat; cat /sys/fs/cgroup/memory.current; "
                         "cat /sys/fs/cgroup/memory.peak; cat /sys/fs/cgroup/io.stat"],
                        capture_output=True, text=True, timeout=10,
                    )
                    native[container] = {
                        "raw": counters.stdout, "status": counters.returncode,
                        "error": counters.stderr,
                    }
                log.write(json.dumps({
                    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "elapsed_s": time.monotonic() - started,
                    "command": command,
                    "stats": [json.loads(line) for line in sample.stdout.splitlines()],
                    "collector_status": sample.returncode,
                    "collector_error": sample.stderr,
                    "native_cgroup": native,
                }) + "\n")
                log.flush()
                time.sleep(1)
        finally:
            if child.poll() is None:
                child.terminate()
            status = child.wait()
    raise SystemExit(status)


if __name__ == "__main__":
    main()
