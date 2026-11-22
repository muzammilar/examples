"""Summarize sampled cgroup counters without inventing absent I/O or reset intervals."""

import statistics


def fields(text):
    return {
        key: int(value) for key, value in (line.split() for line in text.splitlines())
    }


def io_fields(text):
    output = {}
    for line in text.splitlines():
        device, *values = line.split()
        counters = {key: int(value) for key, value in (v.split("=") for v in values)}
        if any(value < 0 for value in counters.values()):
            raise ValueError("negative native I/O counter")
        # Linux permits an all-zero device row containing just its major:minor.
        if values and not {"rbytes", "wbytes", "rios", "wios"} <= counters.keys():
            raise ValueError("incomplete native I/O device row")
        output[device] = {
            key: counters.get(key, 0) for key in ("rbytes", "wbytes", "rios", "wios")
        }
    return output


def summarize(records):
    result = {}
    for role in sorted({r["role"] for r in records if "role" in r}):
        selected = [r for r in records if r.get("role") == role and "counters" in r]
        memory = [int(r["counters"]["memory.current"]) for r in selected]
        cpu, observed_ns, resets = 0, 0, 0
        io = {}
        for before, after in zip(selected, selected[1:]):
            left, right = before["counters"], after["counters"]
            elapsed = right["captured_ns"] - left["captured_ns"]
            if elapsed <= 0 or before["id"] != after["id"]:
                resets += 1
                continue
            delta = (
                fields(right["cpu.stat"])["usage_usec"]
                - fields(left["cpu.stat"])["usage_usec"]
            )
            if delta < 0:
                resets += 1
                continue
            cpu += delta / 1e6
            observed_ns += elapsed
            old, new = io_fields(left["io.stat"]), io_fields(right["io.stat"])
            for device in old.keys() & new.keys():
                if any(new[device][key] < old[device][key] for key in old[device]):
                    resets += 1
                    continue
                total = io.setdefault(
                    device,
                    {
                        "observed_seconds": 0,
                        "rbytes": 0,
                        "wbytes": 0,
                        "rios": 0,
                        "wios": 0,
                    },
                )
                total["observed_seconds"] += elapsed / 1e9
                for key in old[device]:
                    total[key] += new[device][key] - old[device][key]
        for total in io.values():
            for key in ("rbytes", "wbytes", "rios", "wios"):
                total[key + "_per_second"] = total[key] / total["observed_seconds"]
        result[role] = {
            "samples": len(selected),
            "observed_cpu_core_seconds": cpu,
            "average_cpu_percent_one_core_100": cpu / (observed_ns / 1e9) * 100
            if observed_ns
            else None,
            "sampled_memory_bytes_min_mean_max": [
                min(memory),
                statistics.mean(memory),
                max(memory),
            ]
            if memory
            else None,
            "native_peak_memory_bytes": max(
                (int(r["counters"]["memory.peak"]) for r in selected), default=None
            ),
            "state_disk_bytes_baseline_peak_settled": {
                key: [
                    selected[0]["counters"][key],
                    max(r["counters"][key] for r in selected),
                    selected[-1]["counters"][key],
                ]
                for key in ("state_logical_bytes", "state_allocated_bytes")
            }
            if selected
            else {},
            "native_block_io": io or None,
            "counter_reset_or_identity_gaps": resets,
            "scope": "Whole container cgroup, including QEMU/all guest threads and probe costs. About 1 s plus sequential probe intervals; native timestamps retained. CPU excludes initial/final edges and reset gaps; memory peak is lifetime since start. Per-device VM block accounting, not host physical SSD I/O; do not sum stacked/duplicate counters. Absent device rows are unavailable, not zero. Physical I/O latency/queue depth and engine heap unavailable.",
        }
    return result


def summarize_phases(records, boundaries):
    ordered = sorted(boundaries.items(), key=lambda value: value[1])
    return {
        name: {
            "started_ns": start,
            "ended_ns": end,
            "roles": summarize(
                [
                    record
                    for record in records
                    if "counters" in record
                    and start <= record["counters"]["captured_ns"] <= end
                ]
            ),
        }
        for (name, start), (_, end) in zip(ordered, ordered[1:])
    }
