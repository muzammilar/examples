"""Summarize observed Docker counters, keeping measurement gaps explicit."""

import datetime
import json
import re
from pathlib import Path
import sys


def main():
    path = Path(sys.argv[1])
    actors = {}
    peak_total_memory = 0
    for line in path.open():
        row = json.loads(line)
        if row["type"] == "environment":
            environment = row
            continue
        peak_total_memory = max(
            peak_total_memory,
            sum(
                a.get("counters", {}).get("memory_stats", {}).get("usage", 0)
                for a in row["actors"]
            ),
        )
        for actor in row["actors"]:
            if "counters" not in actor:
                continue
            record = actors.setdefault(
                actor["id"],
                {"name": actor["names"][0], "samples": 0, "peak_memory_bytes": 0},
            )
            record["samples"] += 1
            record["memory_sum_bytes"] = record.get("memory_sum_bytes", 0) + actor[
                "counters"
            ].get("memory_stats", {}).get("usage", 0)
            record["peak_memory_bytes"] = max(
                record["peak_memory_bytes"],
                actor["counters"].get("memory_stats", {}).get("usage", 0),
            )
            counters = actor["counters"]
            values = {
                "cpu_core_s": counters.get("cpu_stats", {})
                .get("cpu_usage", {})
                .get("total_usage", 0)
                / 1e9
            }
            for direction in ("rx", "tx"):
                values["network_" + direction + "_bytes"] = sum(
                    interface[direction + "_bytes"]
                    for interface in (counters.get("networks") or {}).values()
                )
            for metric, unit in (
                ("io_service_bytes_recursive", "bytes"),
                ("io_serviced_recursive", "ios"),
            ):
                for value in counters.get("blkio_stats", {}).get(metric) or []:
                    key = "disk_" + value["op"].lower() + "_" + unit
                    values[key] = values.get(key, 0) + value["value"]
            stamp = datetime.datetime.fromisoformat(
                re.sub(r"(\.\d{6})\d+", r"\1", counters["read"].replace("Z", "+00:00"))
            ).timestamp()
            record.setdefault("first_stamp", stamp)
            if "last" in record:
                totals = record.setdefault("counter_deltas", {})
                for key, value in values.items():
                    if key not in record["last"]:
                        continue
                    delta = value - record["last"][key]
                    if delta < 0:
                        record["counter_resets"] = record.get("counter_resets", 0) + 1
                        delta = value
                    totals[key] = totals.get(key, 0) + delta
                if (
                    stamp > record["last_stamp"]
                    and values["cpu_core_s"] >= record["last"]["cpu_core_s"]
                ):
                    utilization = (
                        100
                        * (values["cpu_core_s"] - record["last"]["cpu_core_s"])
                        / (stamp - record["last_stamp"])
                    )
                    record["cpu_utilization_pct_one_core_min"] = min(
                        record.get("cpu_utilization_pct_one_core_min", utilization),
                        utilization,
                    )
                    record["cpu_utilization_pct_one_core_max"] = max(
                        record.get("cpu_utilization_pct_one_core_max", utilization),
                        utilization,
                    )
            record["last"] = values
            record["last_stamp"] = stamp
            if "configuration" in actor:
                record["configuration"] = actor["configuration"]
    for actor in actors.values():
        duration = actor["last_stamp"] - actor.pop("first_stamp")
        actor["observed_s"] = duration
        actor["sampled_mean_memory_bytes"] = (
            actor.pop("memory_sum_bytes") / actor["samples"]
        )
        if duration > 0:
            actor["cpu_utilization_pct_one_core_average"] = (
                100 * actor.get("counter_deltas", {}).get("cpu_core_s", 0) / duration
            )
        actor.pop("last")
        actor.pop("last_stamp")
        actor.setdefault("counter_deltas", {})
    print(
        json.dumps(
            {
                "evidence": str(path),
                "environment": environment,
                "sampled_aggregate_memory_peak_bytes": peak_total_memory,
                "actors": list(actors.values()),
                "limits": [
                    "Sample-window deltas exclude startup and exit outside observation. Decreasing counters are treated as restarts and the new counter is accumulated; reset intervals have no CPU utilization estimate.",
                    "Short-lived actors may be missed; use native pre-exit cgroup counters as well.",
                    "CPU utilization is interval-average percent of one core; short spikes may be missed.",
                    "Docker disk bytes are not device I/O latency; absent counters are unavailable, not zero.",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
