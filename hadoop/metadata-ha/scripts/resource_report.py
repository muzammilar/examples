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
            for value in (
                counters.get("blkio_stats", {}).get("io_service_bytes_recursive") or []
            ):
                key = "disk_" + value["op"].lower() + "_bytes"
                values[key] = values.get(key, 0) + value["value"]
            stamp = datetime.datetime.fromisoformat(
                re.sub(r"(\.\d{6})\d+", r"\1", counters["read"].replace("Z", "+00:00"))
            ).timestamp()
            if "last" in record and stamp > record["last_stamp"]:
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
            record["last_stamp"] = stamp
            record.setdefault("first", values)
            record["last"] = values
            if "configuration" in actor:
                record["configuration"] = actor["configuration"]
    for actor in actors.values():
        last, first = actor.pop("last"), actor.pop("first")
        actor.pop("last_stamp")
        actor["counter_deltas"] = {
            key: value - first.get(key, 0) for key, value in last.items()
        }
    print(
        json.dumps(
            {
                "evidence": str(path),
                "environment": environment,
                "sampled_aggregate_memory_peak_bytes": peak_total_memory,
                "actors": list(actors.values()),
                "limits": [
                    "Sample-window deltas exclude startup and exit outside observation.",
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
