"""Curate native phase deltas and sampled memory without inventing missing counters."""

import json
import datetime
from pathlib import Path
import statistics


def parse(text):
    result = {}
    active = None
    for line in text.splitlines():
        if line.startswith("METRIC:"):
            active = line[7:]
            result.setdefault(active, [])
        elif active and line.strip():
            result.setdefault(active, []).append(line)
    parsed = {}
    for name, lines in result.items():
        if name == "cpu.stat":
            parsed["cpu_us"] = int(dict(line.split() for line in lines)["usage_usec"])
        elif name in ("memory.current", "memory.peak"):
            parsed[name] = int(lines[0])
        elif name == "io.stat":
            io = {key: 0 for key in ("rbytes", "wbytes", "rios", "wios")}
            for line in lines:
                for token in line.split()[1:]:
                    key, value = token.split("=")
                    if key in io:
                        io[key] += int(value)
            parsed.update(io)
    return parsed


def memory_bytes(value):
    units = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": 1024**3,
    }
    for unit in sorted(units, key=len, reverse=True):
        if value.endswith(unit):
            return float(value[: -len(unit)]) * units[unit]
    raise ValueError("Unknown Docker memory unit " + value)


def summarize(path):
    before = json.loads((path / "before-cgroups.json").read_text())
    after = json.loads((path / "after-cgroups.json").read_text())
    scope = "phase end"
    if not after and (path / "pre-stop-cgroups.json").exists():
        after = json.loads((path / "pre-stop-cgroups.json").read_text())
        scope = "through pre-stop snapshot; shutdown tail unavailable"

    delta, resets, born = {}, [], []
    started_ns = json.loads((path / "status.json").read_text())["started_ns"]
    for identifier, end in after.items():
        start = before.get(identifier)
        new_ns = int(
            datetime.datetime.fromisoformat(
                end["started"].replace("Z", "+00:00")
            ).timestamp()
            * 1e9
        )
        if not start and new_ns >= started_ns:
            a = {key: 0 for key in ("cpu_us", "rbytes", "wbytes", "rios", "wios")}
            b = parse(end["output"])
            born.append(end["role"])
            if end["status"]:
                continue
        elif start and start["started"] == end["started"]:
            a, b = parse(start["output"]), parse(end["output"])
            if start["status"] or end["status"]:
                continue
        else:
            resets.append(end["role"])
            continue
        for key in ("cpu_us", "rbytes", "wbytes", "rios", "wios"):
            if key in a and key in b and b[key] >= a[key]:
                delta[key] = delta.get(key, 0) + b[key] - a[key]
    stopped = [
        row["role"] for identifier, row in before.items() if identifier not in after
    ]
    samples, cpu = [], []
    for line in (path / "resources.jsonl").read_text().splitlines():
        row = json.loads(line)
        if "stats" not in row:
            continue
        samples.append(
            round(
                sum(memory_bytes(s["MemUsage"].split(" / ")[0]) for s in row["stats"])
            )
        )
        cpu.append(sum(float(s["CPUPerc"].rstrip("%")) for s in row["stats"]))
    actors = []
    for file in path.glob("*.log"):
        for line in file.read_text(errors="replace").splitlines():
            raw = line.removeprefix("ACTOR_COUNTERS ")
            if not raw.startswith("{"):
                continue
            try:
                row = json.loads(raw)
            except ValueError:
                continue
            data = row.get(
                "actor_counters", row if line.startswith("ACTOR_COUNTERS ") else None
            )
            if data:
                counters = parse(
                    "\n".join("METRIC:" + k + "\n" + v for k, v in data.items())
                )
                actors.append(dict(log=file.name, counters=counters))
    return dict(
        native_retained_role_deltas=delta,
        native_delta_scope=scope,
        restarted_roles_excluded_from_delta=resets,
        roles_stopped_before_final_counter=stopped,
        roles_created_within_phase_from_zero=born,
        actor_lifetime_counters=actors,
        sampled_aggregate_memory_peak_bytes=max(samples) if samples else None,
        sampled_aggregate_memory_mean_bytes=(
            statistics.mean(samples) if samples else None
        ),
        sampled_cpu_peak_percent_one_core_100=max(cpu) if cpu else None,
        sampled_cpu_mean_percent_one_core_100=statistics.mean(cpu) if cpu else None,
        sample_count=len(samples),
        limitations=[
            "Phase native deltas exclude restarted roles; newly created roles use their full within-phase counters. Actor lifetimes are separate.",
            "Docker memory samples use rounded display values and can miss short peaks; native actor memory.peak is separately retained.",
            "Stopped roles without a final counter are unavailable, not zero; shutdown work is not inferred.",
            "No physical device service-time measurement.",
        ],
    )
