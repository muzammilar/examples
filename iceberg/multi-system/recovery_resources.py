"""Example-owned native container counters, recorded outside the object store."""

import json
import os
from pathlib import Path
from decimal import Decimal
import statistics
import subprocess
import threading
import time


PROBE = r"""
set -eu
printf '!started_uptime\n'; cat /proc/uptime
printf '!pid_stat\n'; cat /proc/1/stat
for metric in cpu.stat memory.current memory.peak memory.events memory.stat io.stat; do
  printf '!%s\n' "$metric"
  cat "/sys/fs/cgroup/$metric"
done
printf '!network\n'; cat /proc/net/dev
printf '!allocated_kib\n'
if test -d "$1"; then timeout -s KILL 1 du -sk "$1" 2>/dev/null || printf 'unavailable\n'; else printf 'unavailable\n'; fi
printf '!ended_uptime\n'; cat /proc/uptime
"""


def numeric_fields(text):
    values = {
        key: int(value) for key, value in (line.split() for line in text.splitlines())
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("negative native resource counter")
    return values


def io_fields(text):
    result = {}
    for line in text.splitlines():
        device, *values = line.split()
        counters = dict(item.split("=", 1) for item in values)
        required = {"rbytes", "wbytes", "rios", "wios"}
        if values and not required <= counters.keys():
            raise ValueError("incomplete native I/O device row")
        result[device] = {key: int(counters.get(key, 0)) for key in required}
        if any(value < 0 for value in result[device].values()):
            raise ValueError("negative native I/O counter")
    return result


def allocated_bytes(text):
    lines = text.splitlines()
    if len(lines) != 1:
        return None
    parts = lines[0].split(maxsplit=1)
    return int(parts[0]) * 1024 if len(parts) == 2 and parts[0].isdigit() else None


def parse_probe(output):
    sections, current = {}, None
    for line in output.splitlines():
        if line.startswith("!"):
            current = line[1:]
            if current in sections:
                raise ValueError("duplicate resource section")
            sections[current] = []
        elif current is None:
            raise ValueError("unframed resource output")
        else:
            sections[current].append(line)
    required = {
        "started_uptime",
        "ended_uptime",
        "pid_stat",
        "cpu.stat",
        "memory.current",
        "memory.peak",
        "memory.events",
        "io.stat",
        "network",
        "allocated_kib",
    }
    if not required <= set(sections) <= required | {"memory.stat"}:
        raise ValueError("incomplete resource probe")
    values = {key: "\n".join(lines) for key, lines in sections.items()}
    start, end = [
        int(Decimal(values[key].split()[0]) * 1000000000)
        for key in ("started_uptime", "ended_uptime")
    ]
    if end < start:
        raise ValueError("resource probe clock moved backward")
    network = {}
    for line in values["network"].splitlines():
        if ":" in line:
            interface, counters = line.split(":", 1)
            if interface.strip() != "lo":
                counters = list(map(int, counters.split()))
                network[interface.strip()] = {
                    "rx_bytes": counters[0],
                    "tx_bytes": counters[8],
                }
    result = {
        "native_uptime_start_ns": start,
        "native_uptime_end_ns": end,
        "pid1_start_ticks": int(values["pid_stat"].rsplit(")", 1)[1].split()[19]),
        "cpu": numeric_fields(values["cpu.stat"]),
        "charged_memory_bytes": int(values["memory.current"]),
        "lifetime_peak_memory_bytes": int(values["memory.peak"]),
        "memory_events": numeric_fields(values["memory.events"]),
        "io": io_fields(values["io.stat"]),
        "network": network,
        "allocated_disk_bytes": allocated_bytes(values["allocated_kib"]),
    }
    if "memory.stat" in values:
        result["memory_stat"] = numeric_fields(values["memory.stat"])
    return result


def min_mean_max(values):
    return [min(values), statistics.mean(values), max(values)] if values else None


def summarize(records):
    result = {}
    for role in sorted({record["role"] for record in records}):
        all_rows = [record for record in records if record["role"] == role]
        rows = [record for record in all_rows if record["available"]]
        cpu = {key: 0.0 for key in ("usage_usec", "user_usec", "system_usec")}
        elapsed, gaps, io = 0.0, 0, {}
        for before, after in zip(rows, rows[1:]):
            left, right = before["counters"], after["counters"]
            seconds = (
                right["native_uptime_start_ns"] - left["native_uptime_start_ns"]
            ) / 1e9
            if (
                before["container_id"] != after["container_id"]
                or left["pid1_start_ticks"] != right["pid1_start_ticks"]
                or seconds <= 0
            ):
                gaps += 1
                continue
            deltas = {key: right["cpu"][key] - left["cpu"][key] for key in cpu}
            if any(value < 0 for value in deltas.values()):
                gaps += 1
                continue
            elapsed += seconds
            for key, value in deltas.items():
                cpu[key] += value / 1e6
            for device in left["io"].keys() & right["io"].keys():
                difference = {
                    key: right["io"][device][key] - value
                    for key, value in left["io"][device].items()
                }
                if any(value < 0 for value in difference.values()):
                    gaps += 1
                    continue
                total = io.setdefault(
                    device,
                    {
                        "observed_seconds": 0.0,
                        "rbytes": 0,
                        "wbytes": 0,
                        "rios": 0,
                        "wios": 0,
                    },
                )
                total["observed_seconds"] += seconds
                for key, value in difference.items():
                    total[key] += value
        for values in io.values():
            for key in ("rbytes", "wbytes", "rios", "wios"):
                values[key + "_per_second"] = values[key] / values["observed_seconds"]
        disks = [
            row["counters"]["allocated_disk_bytes"]
            for row in rows
            if row["counters"]["allocated_disk_bytes"] is not None
        ]
        result[role] = {
            "samples": len(rows),
            "unavailable_samples": len(all_rows) - len(rows),
            "observed_seconds": elapsed,
            "cpu_core_seconds": {
                "total": cpu["usage_usec"],
                "user": cpu["user_usec"],
                "system": cpu["system_usec"],
            }
            if elapsed
            else None,
            "cpu_percent_one_core_100": cpu["usage_usec"] / elapsed * 100
            if elapsed
            else None,
            "charged_memory_bytes_min_mean_max": min_mean_max(
                [row["counters"]["charged_memory_bytes"] for row in rows]
            ),
            "lifetime_peak_memory_bytes": max(
                (row["counters"]["lifetime_peak_memory_bytes"] for row in rows),
                default=None,
            ),
            "allocated_disk_bytes_first_peak_last": [disks[0], max(disks), disks[-1]]
            if disks
            else None,
            "native_block_io": io or None,
            "identity_or_counter_gaps": gaps,
            "scope": "Native cgroup CPU across all threads including probes; sampled charged memory and per-generation lifetime peak. Intervals use native /proc/uptime with centisecond resolution; full probe brackets retain uncertainty. Approximate du allocated bytes, not open-deleted or unique physical storage. Workload /results binds share one witness directory: do not sum them. Device I/O is Docker-VM accounting, not physical SSD latency/queue depth; do not sum stacked devices. Missing devices/counters are unavailable, not zero. CPU and byte/IOPS deltas exclude restart bridges and unobserved initial/final edges. Raw samples retain network counters and timing uncertainty.",
        }
        memory = [
            row["counters"]["memory_stat"]
            for row in rows
            if "memory_stat" in row["counters"]
        ]
        if memory:
            # Preserve exact legacy summaries: old samples lack these counters.
            result[role]["memory_stat_bytes_min_mean_max"] = {
                key: min_mean_max([sample[key] for sample in memory if key in sample])
                for key in ("anon", "file", "kernel")
            }
            result[role]["memory_stat_samples"] = len(memory)
            result[role]["memory_stat_scope"] = (
                "Sampled native cgroup byte gauges, not Go heap or process RSS. "
                "File includes charged file cache; kernel includes its subcategories. "
                "Counters are read nonatomically; do not infer an exact partition of memory.current."
            )
    return result


def require_coverage(records, roles, since_ns):
    selected = [record for record in records if record["host_started_ns"] >= since_ns]
    summary = summarize(selected)
    for role in roles:
        if (
            role not in summary
            or summary[role]["observed_seconds"] <= 0
            or summary[role]["charged_memory_bytes_min_mean_max"] is None
        ):
            raise RuntimeError("missing healthy-phase resource coverage: " + role)


class ResourceSampler:
    def __init__(self, directory, *, env=None):
        self.directory = Path(directory)
        self.env = env
        self.targets, self.errors = {}, []
        self.lock, self.stop = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self.sample, daemon=True)

    def bind(self, role, container_id, disk_root):
        with self.lock:
            self.targets[role] = (container_id, disk_root)

    def start(self):
        self.thread.start()

    def unbind(self, role):
        with self.lock:
            self.targets.pop(role, None)

    def records(self):
        body = (self.directory / "resources.jsonl").read_bytes()
        if body and not body.endswith(b"\n"):
            body = body.rpartition(b"\n")[0]
        return [json.loads(line) for line in body.splitlines()]

    def require_coverage(self, roles, since_ns):
        require_coverage(self.records(), roles, since_ns)

    def sample(self):
        try:
            with (self.directory / "resources.jsonl").open("x") as output:
                while not self.stop.is_set():
                    with self.lock:
                        targets = dict(self.targets)
                    for role, (identifier, disk_root) in targets.items():
                        if self.stop.is_set():
                            break
                        record = {
                            "role": role,
                            "container_id": identifier,
                            "host_started_ns": time.time_ns(),
                        }
                        probe = None
                        try:
                            probe = subprocess.run(
                                [
                                    "docker",
                                    "exec",
                                    identifier,
                                    "timeout",
                                    "-s",
                                    "KILL",
                                    "4",
                                    "sh",
                                    "-c",
                                    PROBE,
                                    "sh",
                                    disk_root,
                                ],
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=6,
                                env=self.env,
                            )
                            record.update(
                                available=True, counters=parse_probe(probe.stdout)
                            )
                        except (
                            subprocess.SubprocessError,
                            ValueError,
                            KeyError,
                            IndexError,
                            ArithmeticError,
                        ) as error:
                            record.update(
                                available=False,
                                error=str(error),
                                stderr=str(getattr(error, "stderr", ""))[-2048:],
                                stdout=str(
                                    getattr(error, "stdout", None)
                                    or (probe.stdout if probe else "")
                                )[-4096:],
                            )
                        record["host_ended_ns"] = time.time_ns()
                        output.write(json.dumps(record) + "\n")
                    output.flush()
                    os.fsync(output.fileno())
                    self.stop.wait(1)
        except BaseException as error:
            self.errors.append(repr(error))

    def close(self):
        self.stop.set()
        self.thread.join(10)
        if self.thread.is_alive() or self.errors:
            raise RuntimeError(
                "resource sampler failed to finish: " + repr(self.errors)
            )
        records = self.records()
        return records, summarize(records)
