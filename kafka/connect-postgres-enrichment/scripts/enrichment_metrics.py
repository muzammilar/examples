"""Docker-native cumulative CPU and sampled memory; no host-wide monitoring."""

import gzip
import http.client
import json
import os
import socket
import threading
import time
import urllib.parse

import enrichment_lab as lab


class DockerHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=10)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def storage_snapshot():
    """Allocated bytes in each owned persistent service mount, without host scans."""
    rows = {}
    for role in lab.BASE:
        state = lab.service(role)
        paths = [m["Destination"] for m in state["Mounts"] if m["Type"] == "volume"]
        if not paths:
            continue
        if not state["State"]["Running"]:
            rows[role] = {"available": False, "reason": "container stopped"}
            continue
        output = lab.call(
            ["docker", "exec", state["Id"], "du", "-sk", *paths], timeout=20
        )
        mounts = {}
        for line in output.splitlines():
            size, path = line.split(maxsplit=1)
            mounts[path] = int(size) * 1024
        if set(mounts) != set(paths):
            raise RuntimeError("incomplete persistent-volume usage")
        rows[role] = {"available": True, "allocated_bytes": mounts}
    return {
        "time": time.time(),
        "roles": rows,
        "scope": "du allocated KiB converted to bytes; persistent owned mounts, not shared filesystem capacity. Phase snapshots can miss a transient peak.",
    }


class Recorder:
    def __init__(self, root):
        endpoint = (
            os.environ.get("DOCKER_HOST")
            or json.loads(lab.call(["docker", "context", "inspect"]))[0]["Endpoints"][
                "docker"
            ]["Host"]
        )
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme != "unix":
            raise RuntimeError(
                "resource recorder requires the active local Unix Docker API"
            )
        self.socket = urllib.parse.unquote(parsed.path)
        self.path = root / "resources.jsonl.gz"
        self.stop_event = threading.Event()
        self.error = None
        self.thread = threading.Thread(target=self.run, name="docker-resource-recorder")

    def get(self, route, *, missing=False):
        connection = DockerHTTP(self.socket)
        try:
            connection.request("GET", route)
            response = connection.getresponse()
            if response.status == 404 and missing:
                return None
            if response.status != 200:
                raise RuntimeError(f"Docker stats HTTP {response.status}")
            return json.load(response)
        finally:
            connection.close()

    def start(self):
        self.thread.start()

    def check(self):
        if self.error:
            raise RuntimeError("resource recorder failed") from self.error

    def run(self):
        try:
            with (
                self.path.open("xb") as fd,
                gzip.GzipFile(fileobj=fd, mode="wb", compresslevel=1) as stream,
            ):
                first = True
                while True:
                    start = time.monotonic()
                    filters = urllib.parse.quote(
                        json.dumps(
                            {"label": ["com.docker.compose.project=" + lab.PROJECT]}
                        )
                    )
                    rows = self.get("/containers/json?all=true&filters=" + filters)
                    for row in rows:
                        identity = row["Id"]
                        before = self.get(
                            "/containers/" + identity + "/json", missing=True
                        )
                        if before is None:
                            continue
                        if before["Config"]["Labels"].get(
                            "com.docker.compose.project.working_dir"
                        ) != str(lab.ROOT):
                            raise RuntimeError("unowned statistics target")
                        if not before["State"]["Running"]:
                            continue
                        stats = self.get(
                            "/containers/"
                            + identity
                            + "/stats?stream=false&one-shot=true"
                        )
                        after = self.get(
                            "/containers/" + identity + "/json", missing=True
                        )
                        if after is None:
                            continue
                        if (
                            before["State"]["StartedAt"] != after["State"]["StartedAt"]
                            or not after["State"]["Running"]
                        ):
                            continue
                        event = {
                            "time": time.time(),
                            "id": identity,
                            "start": before["State"]["StartedAt"],
                            "initial": first,
                            "role": before["Config"]["Labels"][
                                "com.docker.compose.service"
                            ],
                            "stats": stats,
                            "cpu_limit_cores": before["HostConfig"].get("NanoCpus", 0)
                            / 1e9,
                        }
                        stream.write((json.dumps(event) + "\n").encode())
                    stream.flush()
                    fd.flush()
                    os.fsync(fd.fileno())
                    first = False
                    if self.stop_event.wait(max(0, 5 - (time.monotonic() - start))):
                        break
        except Exception as error:
            self.error = error

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=45)
        if self.thread.is_alive():
            raise RuntimeError("statistics thread did not stop")
        self.check()


def summarize(path):
    rows = {}
    samples = 0
    with gzip.open(path, "rt") as stream:
        for line in stream:
            e = json.loads(line)
            s = e["stats"]
            key = e["id"] + "/" + e["start"]
            cpu = s["cpu_stats"]["cpu_usage"]["total_usage"]
            row = rows.setdefault(
                key,
                {
                    "role": e["role"],
                    "initial_cpu": cpu if e["initial"] else 0,
                    "last_cpu": 0,
                    "sampled_max_charged_bytes": 0,
                    "sampled_max_anonymous_bytes": 0,
                    "memory_samples": [],
                    "cpu_intervals": [],
                    "previous_time": None,
                    "previous_cpu": None,
                    "io_first": None,
                    "io_last": None,
                    "sample_first_io": None,
                    "cpu_limit_cores": e.get("cpu_limit_cores"),
                },
            )
            if cpu < row["last_cpu"]:
                raise RuntimeError(
                    "CPU counter regressed within same process incarnation"
                )
            timestamp = e.get("time")
            if row["previous_time"] is not None and timestamp is not None:
                elapsed = timestamp - row["previous_time"]
                if elapsed <= 0:
                    raise RuntimeError("resource timestamps did not advance")
                row["cpu_intervals"].append(
                    ((cpu - row["previous_cpu"]) / 1e9, elapsed)
                )
            row["previous_time"] = timestamp
            row["previous_cpu"] = cpu
            row["last_cpu"] = cpu
            memory = s.get("memory_stats", {})
            detail = memory.get("stats", {})
            row["sampled_max_charged_bytes"] = max(
                row["sampled_max_charged_bytes"], memory.get("usage", 0)
            )
            row["sampled_max_anonymous_bytes"] = max(
                row["sampled_max_anonymous_bytes"],
                detail.get("anon", detail.get("rss", 0)),
            )
            if "usage" in memory:
                row["memory_samples"].append(memory["usage"])
            block = s.get("blkio_stats", {})
            counters = {}
            for field, suffix in [
                ("io_service_bytes_recursive", "bytes"),
                ("io_serviced_recursive", "operations"),
            ]:
                entries = block.get(field)
                for operation in ("Read", "Write"):
                    matching = [
                        x["value"]
                        for x in (entries or [])
                        if x["op"].lower() == operation.lower()
                    ]
                    counters[operation.lower() + "_" + suffix] = (
                        sum(matching) if matching else None
                    )
            if row["io_first"] is None:
                row["io_first"] = {
                    k: v if e["initial"] else 0 if v is not None else None
                    for k, v in counters.items()
                }
                row["sample_first_io"] = counters
            row["io_last"] = counters
            samples += 1
    for row in rows.values():
        row["cpu_core_seconds"] = (row["last_cpu"] - row.pop("initial_cpu")) / 1e9
        intervals = row.pop("cpu_intervals")
        seconds = sum(t for _, t in intervals)
        row["cpu_utilization_percent_one_core_100"] = {
            "average": sum(c for c, _ in intervals) / seconds * 100
            if seconds
            else None,
            "sampled_peak": max((c / t * 100 for c, t in intervals), default=None),
        }
        memory = row.pop("memory_samples")
        row["sampled_mean_charged_bytes"] = (
            sum(memory) / len(memory) if memory else None
        )
        first, last = row.pop("io_first"), row.pop("io_last")
        sample_first = row.pop("sample_first_io")
        row["block_io"] = {}
        row["block_io_rates_per_second"] = {}
        for key, value in last.items():
            initial = first[key]
            delta = (
                value - initial if value is not None and initial is not None else None
            )
            if delta is not None and delta < 0:
                raise RuntimeError("block I/O counter regressed")
            row["block_io"][key] = delta
            sample = sample_first[key]
            row["block_io_rates_per_second"][key] = (
                (value - sample) / seconds
                if value is not None and sample is not None and seconds
                else None
            )
        row.pop("previous_time")
        row.pop("previous_cpu")
    return {
        "samples": samples,
        "cadence_target_seconds": 5,
        "containers": rows,
        "cpu_core_seconds": sum(r["cpu_core_seconds"] for r in rows.values()),
        "limits": "Cgroup CPU across all threads; one core = 100%. Memory mean is a sample mean; maxima are sampled charged/anonymous usage, not process peak RSS or simultaneous stack peak. Block I/O is runtime cgroup accounting, not logical engine I/O; empty counters are unavailable. I/O latency/queue depth unavailable. Stopped tails and short-lived work can be missed. Host controllers/Docker VM overhead excluded.",
    }
