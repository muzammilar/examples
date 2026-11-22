"""Read exact Docker counters; sampling gaps remain explicit observations."""

import http.client
import json
import socket
import threading
import time


def parse_native_io(text):
    devices = {}
    required = {"rbytes", "wbytes", "rios", "wios"}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        device = fields[0]
        if (
            len(device.split(":")) != 2
            or not all(part.isdigit() for part in device.split(":"))
            or device in devices
        ):
            raise ValueError("invalid/duplicate native I/O device")
        pairs = [field.split("=", 1) for field in fields[1:]]
        raw = dict(pairs)
        if len(raw) != len(pairs):
            raise ValueError("duplicate native I/O counter")
        # Linux can list a device without fields when all counters are zero.
        if not fields[1:]:
            raw = dict.fromkeys(required, "0")
        if not required <= raw.keys():
            raise ValueError("incomplete native I/O counters")
        values = {key: int(raw[key]) for key in required}
        if any(value < 0 for value in values.values()):
            raise ValueError("negative native I/O counter")
        devices[device] = values
    return devices


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=3)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class Recorder:
    def __init__(self, path, endpoint, targets, disk_probe=None):
        if not endpoint.startswith("unix://"):
            raise ValueError("resource monitoring requires a local Unix Docker API")
        self.path, self.endpoint, self.targets = path, endpoint[7:], targets
        self.disk_probe = disk_probe
        self.phase = "setup"
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.error = None

    def __enter__(self):
        self.thread.start()
        return self

    def run(self):
        try:
            with self.path.open("x") as out:
                while not self.stop.is_set():
                    for role, identity in self.targets:
                        record = {
                            "role": role,
                            "container": identity,
                            "phase": self.phase,
                            "started_ns": time.time_ns(),
                        }
                        connection = UnixHTTP(self.endpoint)
                        try:
                            connection.request(
                                "GET",
                                f"/containers/{identity}/stats?stream=false&one-shot=true",
                            )
                            response = connection.getresponse()
                            if response.status != 200:
                                raise RuntimeError(
                                    f"Docker stats HTTP {response.status}: {response.read(1000)!r}"
                                )
                            record["stats"] = json.load(response)
                        except (
                            OSError,
                            ValueError,
                            http.client.HTTPException,
                            RuntimeError,
                        ) as error:
                            record["unavailable"] = str(error)
                        finally:
                            connection.close()
                        record["ended_ns"] = time.time_ns()
                        if self.disk_probe is not None:
                            try:
                                disk = self.disk_probe(role, identity)
                                if disk is not None:
                                    record["disk"] = disk
                            except Exception as error:
                                record["disk_unavailable"] = str(error)
                        out.write(json.dumps(record) + "\n")
                        out.flush()
                    self.stop.wait(1)
        except Exception as error:
            self.error = error

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("resource sampler did not stop")
        if self.error:
            raise self.error


def summarize(path):
    return summarize_records(
        [json.loads(line) for line in path.read_text().splitlines()]
    )


def summarize_records(records):
    output = {}
    for role in sorted({r["role"] for r in records}):
        samples = [r for r in records if r["role"] == role and "stats" in r]
        usable = [
            r
            for r in samples
            if r["stats"].get("memory_stats", {}).get("usage") is not None
        ]
        memory = [r["stats"]["memory_stats"]["usage"] for r in usable]
        cpu_seconds, cpu_percent, restarts, observed_seconds = 0, [], 0, 0
        for before, after in zip(samples, samples[1:]):
            a = (
                before["stats"]
                .get("cpu_stats", {})
                .get("cpu_usage", {})
                .get("total_usage")
            )
            b = (
                after["stats"]
                .get("cpu_stats", {})
                .get("cpu_usage", {})
                .get("total_usage")
            )
            if a is None or b is None:
                continue
            if b < a:
                restarts += 1
                continue
            delta = (b - a) / 1e9
            elapsed = (after["ended_ns"] - before["ended_ns"]) / 1e9
            cpu_seconds += delta
            if elapsed > 0:
                cpu_percent.append(delta / elapsed * 100)
                observed_seconds += elapsed
        counter_series = {}
        disk = {}
        for record in samples:
            stats = record["stats"]
            counters = {}
            for key, suffix in (
                ("io_service_bytes_recursive", "bytes"),
                ("io_serviced_recursive", "operations"),
            ):
                for item in (stats.get("blkio_stats") or {}).get(key) or []:
                    operation = item["op"].lower()
                    if operation in {"read", "write"}:
                        counters[
                            f"block_{item['major']}:{item['minor']}_{operation}_{suffix}"
                        ] = item["value"]
            for interface, values in (stats.get("networks") or {}).items():
                for key in ("rx_bytes", "tx_bytes"):
                    if key in values:
                        counters[f"network_{interface}_{key}"] = values[key]
            for key, value in counters.items():
                counter_series.setdefault(key, []).append((record["ended_ns"], value))
            native = record.get("disk", {})
            for device, values in native.get("native_io", {}).items():
                for field, suffix in (
                    ("rbytes", "read_bytes"),
                    ("wbytes", "write_bytes"),
                    ("rios", "read_operations"),
                    ("wios", "write_operations"),
                ):
                    counter_series.setdefault(
                        "native_block_" + device + "_" + suffix, []
                    ).append((native["native_io_captured_ns"], values[field]))
            for path, values in record.get("disk", {}).get("paths", {}).items():
                disk.setdefault(path, []).append(values)
        io = {}
        for key, values in counter_series.items():
            delta, seconds, resets = 0, 0, 0
            for a, b in zip(values, values[1:]):
                if b[1] < a[1]:
                    resets += 1
                else:
                    delta += b[1] - a[1]
                    seconds += (b[0] - a[0]) / 1e9
            io[key] = {
                "observed_delta": delta if seconds else None,
                "observed_seconds": seconds,
                "per_second": delta / seconds if seconds else None,
                "counter_resets": resets,
            }
        output[role] = {
            "samples": len(samples),
            "unavailable_samples": sum(
                r["role"] == role and "unavailable" in r for r in records
            ),
            "observed_cpu_core_seconds": cpu_seconds if observed_seconds else None,
            "average_cpu_percent_one_core_100_observed_intervals": cpu_seconds
            / observed_seconds
            * 100
            if observed_seconds
            else None,
            "cpu_percent_one_core_100_min_max": [min(cpu_percent), max(cpu_percent)]
            if cpu_percent
            else None,
            "sampled_charged_memory_bytes_min_mean_max": [
                min(memory),
                sum(memory) / len(memory),
                max(memory),
            ]
            if memory
            else None,
            "observed_counter_resets": restarts,
            "io_and_network": io,
            "io_scope": "Kernel per-device and per-interface counters, with reset intervals excluded. Missing fields are unavailable, not zero. Do not sum stacked devices or both endpoints into physical traffic; raw counters remain authoritative.",
            "sampled_disk_bytes_min_max": {
                path: {
                    key: [min(v[key] for v in values), max(v[key] for v in values)]
                    for key in ("logical_bytes", "allocated_bytes")
                }
                for path, values in disk.items()
            },
            "scope": "About 1s cadence. CPU covers deltas between usable native samples; restart/reset intervals and initial/final partial intervals are excluded, not estimated. Raw block I/O and all native fields remain in JSONL. This is not process RSS or a complete CPU total across crashes.",
        }
    return output
