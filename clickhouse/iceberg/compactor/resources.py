"""Native observations for this one-shot Spark job; no external metrics tools."""

import json
import os
from pathlib import Path
import statistics
import threading
import time


def observed(read):
    try:
        return {"available": True, "value": read()}
    except (OSError, ValueError, IndexError, KeyError) as error:
        return {"available": False, "error": repr(error)}


def fields(text):
    values = {
        key: int(value) for key, value in (line.split() for line in text.splitlines())
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("negative native counter")
    return values


def io_devices(text):
    devices = {}
    for line in text.splitlines():
        device, *values = line.split()
        parsed = dict(value.split("=", 1) for value in values)
        names = ("rbytes", "wbytes", "rios", "wios")
        if values and not set(names) <= parsed.keys():
            raise ValueError("incomplete native IO device counters")
        devices[device] = {name: int(parsed.get(name, 0)) for name in names}
        if any(value < 0 for value in devices[device].values()):
            raise ValueError("negative native IO counter")
    return devices


def process_io(path):
    value = fields(path.read_text().replace(":", ""))
    if not {"rchar", "wchar", "read_bytes", "write_bytes"} <= value.keys():
        raise ValueError("incomplete native process IO counters")
    return value


def process(pid, proc=Path("/proc")):
    def read():
        directory = proc / str(pid)
        stat = (directory / "stat").read_text()
        prefix, _, suffix = stat.rpartition(")")
        values = suffix.split()
        status = observed(lambda: (directory / "status").read_text())
        peak = None
        if status["available"]:
            for line in status["value"].splitlines():
                if line.startswith("VmHWM:"):
                    peak = int(line.split()[1]) * 1024
        return {
            "pid": pid,
            "command": prefix.split("(", 1)[1],
            "start_ticks": int(values[19]),
            "cpu_ticks": {"user": int(values[11]), "system": int(values[12])},
            "rss_bytes": int(values[21]) * os.sysconf("SC_PAGE_SIZE"),
            "peak_rss_bytes": peak,
            "status": status,
            "io": observed(lambda: process_io(directory / "io")),
        }

    return observed(read)


def snapshot(phase, jvm_pid, proc=Path("/proc"), cgroup=Path("/sys/fs/cgroup")):
    def scalar(name):
        value = int((cgroup / name).read_text())
        if value < 0:
            raise ValueError("negative native memory counter")
        return value

    space = {}
    for name in ("/tmp", "/results"):

        def filesystem(path=name):
            value = os.statvfs(path)
            return {
                "capacity_bytes": value.f_blocks * value.f_frsize,
                "free_bytes": value.f_bfree * value.f_frsize,
                "available_bytes": value.f_bavail * value.f_frsize,
            }

        space[name] = observed(filesystem)
    return {
        "utc_ns": time.time_ns(),
        "monotonic_ns": time.monotonic_ns(),
        "phase": phase,
        "container": {
            "limits": {
                name: observed(lambda name=name: (cgroup / name).read_text().strip())
                for name in ("cpu.max", "memory.max", "memory.swap.max")
            },
            "cpu": observed(lambda: fields((cgroup / "cpu.stat").read_text())),
            "memory_current_bytes": observed(lambda: scalar("memory.current")),
            "memory_peak_bytes": observed(lambda: scalar("memory.peak")),
            "memory_events": observed(
                lambda: fields((cgroup / "memory.events").read_text())
            ),
            "io": observed(lambda: io_devices((cgroup / "io.stat").read_text())),
        },
        "python": process(os.getpid(), proc),
        "jvm": process(jvm_pid, proc)
        if jvm_pid is not None
        else {"available": False, "error": "JVM PID not identified"},
        "filesystem_space": space,
    }


def statistics_or_none(values):
    return (
        {"min": min(values), "mean": statistics.mean(values), "max": max(values)}
        if values
        else None
    )


def summarize(records):
    result = {
        "samples": len(records),
        "clock_ticks_per_second": os.sysconf("SC_CLK_TCK"),
    }
    for role in ("container", "python", "jvm"):
        cpu_seconds, elapsed, utilization, gaps = 0.0, 0.0, [], 0
        resolution = (
            1e-6 if role == "container" else 1 / result["clock_ticks_per_second"]
        )
        minimum_interval = 0.0 if role == "container" else 10 * resolution
        short_intervals = 0
        io_totals = {}
        memory, peaks, generations = [], [], set()
        available = []
        for record in records:
            raw = record[role]
            value = raw if role == "container" else raw.get("value", {})
            if role == "container":
                cpu = value["cpu"]
                if value["memory_current_bytes"]["available"]:
                    memory.append(value["memory_current_bytes"]["value"])
                if value["memory_peak_bytes"]["available"]:
                    peaks.append(value["memory_peak_bytes"]["value"])
                if cpu["available"]:
                    available.append(
                        (
                            record["monotonic_ns"],
                            None,
                            cpu["value"]["usage_usec"] / 1e6,
                            value["io"],
                        )
                    )
            elif raw["available"]:
                generation = (value["pid"], value["start_ticks"])
                generations.add(generation)
                memory.append(value["rss_bytes"])
                if value["peak_rss_bytes"] is not None:
                    peaks.append(value["peak_rss_bytes"])
                available.append(
                    (
                        record["monotonic_ns"],
                        generation,
                        sum(value["cpu_ticks"].values())
                        / result["clock_ticks_per_second"],
                        value["io"],
                    )
                )
        for left, right in zip(available, available[1:]):
            seconds, delta = (right[0] - left[0]) / 1e9, right[2] - left[2]
            if left[1] != right[1] or seconds <= 0 or delta < 0:
                gaps += 1
                continue
            elapsed += seconds
            cpu_seconds += delta
            # /proc process CPU is clock-tick quantized. Manual captures can
            # be only milliseconds apart: retain their counters but do not turn
            # one tick into a misleading instantaneous utilization spike.
            if seconds >= minimum_interval:
                utilization.append(100 * delta / seconds)
            else:
                short_intervals += 1
            if left[3]["available"] and right[3]["available"]:
                before, after = left[3]["value"], right[3]["value"]
                # Empty io.stat means no reported devices, not measured zero IO.
                before = before if role == "container" else {"process": before}
                after = after if role == "container" else {"process": after}
                for device in before.keys() & after.keys():
                    changes = {
                        key: after[device][key] - value
                        for key, value in before[device].items()
                    }
                    if any(value < 0 for value in changes.values()):
                        gaps += 1
                        continue
                    total = io_totals.setdefault(
                        device, {"observed_seconds": 0.0, "counters": {}}
                    )
                    total["observed_seconds"] += seconds
                    for key, value in changes.items():
                        total["counters"][key] = total["counters"].get(key, 0) + value
        for device in io_totals.values():
            device["per_second"] = {
                key: value / device["observed_seconds"]
                for key, value in device["counters"].items()
            }
        result[role] = {
            "observed_seconds": elapsed,
            "cpu_core_seconds": cpu_seconds if elapsed else None,
            "cpu_percent_one_core_100": 100 * cpu_seconds / elapsed
            if elapsed and elapsed >= minimum_interval
            else None,
            "cpu_counter_resolution_seconds": resolution,
            "cpu_utilization_min_interval_seconds": minimum_interval,
            "cpu_short_intervals_suppressed": short_intervals,
            "cpu_interval_percent_min_mean_max": statistics_or_none(utilization),
            "sampled_memory_bytes_min_mean_max": statistics_or_none(memory),
            "lifetime_peak_memory_bytes": max(peaks) if peaks else None,
            "generations": [
                {"pid": pid, "start_ticks": start} for pid, start in sorted(generations)
            ],
            "io": io_totals or None,
            "identity_or_counter_gaps": gaps,
        }
    result["scope"] = (
        "Container cgroup v2 totals include the JVM and Python, including any JVM startup before Python entry; deltas cover observed intervals only. Process CPU/IO deltas never bridge a changed PID/start-tick generation. Process CPU counters have 1/SC_CLK_TCK-second resolution: interval utilization requires at least 10 ticks of elapsed time, and whole-run utilization requires the same minimum total observed duration. Short intervals are counted and excluded only from utilization peaks/interval summaries, never CPU core-seconds or raw counters. Cgroup CPU has microsecond counter resolution and uses its observed intervals without the process tick threshold. Cgroup charged memory includes page cache; Python/JVM RSS and lifetime peaks are separate and must not be summed. Memory means are sample means. /tmp and /results statvfs values are shared-filesystem capacity/free space, not per-job disk allocation. Block IO counters are container/VM accounting, not physical-drive latency; devices are separate and stacked devices must not be summed; Python/JVM rchar/wchar are syscall bytes, not disk bytes. Missing counters/devices remain unavailable, not zero."
    )
    return result


class Sampler:
    def __init__(self, path, interval=1):
        self.path, self.interval = Path(path), interval
        self.jvm_pid = None
        self.jvm_identity_source = "unidentified"
        self.records, self.errors = [], []
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)
        self.stream = self.path.open("x")
        try:
            descriptor = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            self.stream.close()
            raise

    def capture(self, phase):
        with self.lock:
            row = snapshot(phase, self.jvm_pid)
            row["jvm_identity_source"] = self.jvm_identity_source
            self.stream.write(json.dumps(row) + "\n")
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.records.append(row)
            return row

    def start(self):
        self.capture("python-entry-before-spark-session")
        self.thread.start()

    def attach_parent_jvm(self):
        # spark-submit normally launches Python from its Java driver process.
        # Keep startup-failure observations without guessing an unrelated PID.
        parent = process(os.getppid())
        if parent["available"] and parent["value"]["command"] == "java":
            with self.lock:
                self.jvm_pid = os.getppid()
                self.jvm_identity_source = "native-java-parent-of-spark-submit-python"
            self.capture("java-parent-before-spark-session")

    def attach_jvm(self, pid):
        if type(pid) is not int or pid <= 0 or pid == os.getpid():
            raise RuntimeError("invalid native JVM PID")
        with self.lock:
            if self.jvm_pid is not None and self.jvm_pid != pid:
                raise RuntimeError("native JVM PID differs from observed Java parent")
            self.jvm_pid = pid
            self.jvm_identity_source = "spark-runtime-mxbean-getPid"
        row = self.capture("spark-session-ready")
        if not row["jvm"]["available"]:
            raise RuntimeError("cannot observe the live native JVM")

    def _sample(self):
        try:
            while not self.stop_event.wait(self.interval):
                self.capture("running")
        except BaseException as error:
            self.errors.append(error)

    def close(self):
        self.stop_event.set()
        if self.thread.ident is not None:
            self.thread.join(10)
        if self.thread.is_alive():
            raise RuntimeError("resource sampler did not stop")
        try:
            self.capture("after-spark-stop-attempt")
        finally:
            self.stream.close()
        if self.errors:
            raise RuntimeError("resource sampler failed: " + repr(self.errors))
        return summarize(self.records)
