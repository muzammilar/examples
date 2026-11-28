"""Sample raw Docker resource counters for every owned role, including scratch jobs."""

import datetime
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote

PROJECT = os.environ.get("COMPOSE_PROJECT_NAME", "obs-three-signal-incident")


class DockerConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=15)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def main():
    endpoint = subprocess.check_output(
        [
            "docker",
            "context",
            "inspect",
            "--format",
            '{{(index .Endpoints "docker").Host}}',
        ],
        text=True,
    ).strip()
    if not endpoint.startswith("unix://"):
        raise SystemExit("This isolated local observer requires a Unix Docker endpoint")
    socket_path = endpoint.removeprefix("unix://")

    def get(path):
        connection = DockerConnection(socket_path)
        try:
            connection.request("GET", path)
            response = connection.getresponse()
            data = response.read()
            if response.status != 200:
                raise RuntimeError(
                    f"Docker {path}: HTTP {response.status}: {data[:300]!r}"
                )
            return json.loads(data)
        finally:
            connection.close()

    version = get("/version")
    info = get("/info")
    environment = {
        key: info.get(key)
        for key in (
            "NCPU",
            "MemTotal",
            "KernelVersion",
            "OperatingSystem",
            "Driver",
            "ServerVersion",
        )
    }
    environment["docker_api"] = version["ApiVersion"]
    filters = quote(json.dumps({"label": [f"com.docker.compose.project={PROJECT}"]}))
    results = Path("results")
    results.mkdir(exist_ok=True)
    command = (
        [sys.executable, "-c", "import time; time.sleep(180)"]
        if sys.argv[1:] == ["--observe-only"]
        else ["make", *sys.argv[1:]]
    )
    run_id = time.time_ns()
    started = time.monotonic()
    seen = set()
    with (results / f"resources-{run_id}.jsonl").open("x") as log:
        log.write(
            json.dumps(
                {
                    "type": "environment",
                    "run_id": run_id,
                    "fixture": (
                        json.loads((results / "plan.json").read_text()).get("corpus")
                        if (results / "plan.json").exists()
                        else None
                    ),
                    "command": command,
                    "environment": environment,
                    "cotenancy": "Only this three-signal comparison; backends run serially; no other heavy lab.",
                }
            )
            + "\n"
        )
        log.flush()
        child = subprocess.Popen(command)
        try:
            while child.poll() is None:
                sample = {
                    "type": "resources",
                    "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "elapsed_s": time.monotonic() - started,
                    "phase": (
                        (results / "phase").read_text().strip()
                        if (results / "phase").exists()
                        else "setup"
                    ),
                    "actors": [],
                }
                for container in get("/containers/json?filters=" + filters):
                    identifier = container["Id"]
                    actor = {"id": identifier, "names": container["Names"]}
                    try:
                        if identifier not in seen:
                            metadata = get(f"/containers/{identifier}/json")
                            actor["configuration"] = {
                                "image_id": metadata["Image"],
                                "image_reference": metadata["Config"]["Image"],
                                "memory_limit_bytes": metadata["HostConfig"]["Memory"],
                                "nano_cpus": metadata["HostConfig"]["NanoCpus"],
                                "started_at": metadata["State"]["StartedAt"],
                            }
                            seen.add(identifier)
                        stats = get(
                            f"/containers/{identifier}/stats?stream=false&one-shot=true"
                        )
                        actor["counters"] = {
                            key: stats.get(key)
                            for key in (
                                "read",
                                "cpu_stats",
                                "memory_stats",
                                "blkio_stats",
                                "networks",
                                "pids_stats",
                            )
                        }
                    except (OSError, RuntimeError, http.client.HTTPException) as error:
                        # A short-lived actor may disappear between listing and reading stats.
                        actor["collector_error"] = str(error)
                    sample["actors"].append(actor)
                log.write(json.dumps(sample) + "\n")
                log.flush()
                time.sleep(1)
        finally:
            if child.poll() is None:
                child.terminate()
            status = child.wait()
    raise SystemExit(status)


if __name__ == "__main__":
    main()
