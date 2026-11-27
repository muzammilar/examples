"""Summarize one observed HA case; raw logs remain immutable under results/."""

import json
import math
from pathlib import Path
import sys

import lab


def main():
    directory = Path(sys.argv[1]).resolve()
    directory.relative_to(lab.ROOT / "results")
    result = {
        "evidence": str(directory.relative_to(lab.ROOT)),
        "timing_kind": "diagnostic",
    }
    if (directory / "result.json").exists():
        result["verdict"] = json.loads((directory / "result.json").read_text())
        if "roles_after_rejoin" in result["verdict"]:
            result["verdict"]["roles_after_rejoin"] = {
                node: info["State"]
                for node, info in result["verdict"]["roles_after_rejoin"].items()
            }
    operations = {}
    for name in ("writer", "reader"):
        path = directory / (name + ".log")
        records = (
            [
                json.loads(line)
                for line in path.read_text().splitlines()
                if line.startswith("{")
            ]
            if path.exists()
            else []
        )
        rows = [r for r in records if r.get("event") in ("ack", "ambiguous", "read")]
        durations = sorted(r["duration_ms"] for r in rows)
        operations[name] = {
            "outcomes": {
                kind: sum(r.get("result", r["event"]) == kind for r in rows)
                for kind in ("ack", "ambiguous", "ok", "fail")
            },
            "latency_ms": (
                {
                    "samples": len(durations),
                    "min": min(durations),
                    "max": max(durations),
                    **{
                        label: durations[
                            min(
                                len(durations) - 1,
                                max(0, math.ceil(len(durations) * fraction) - 1),
                            )
                        ]
                        for label, fraction in (
                            ("p50", 0.5),
                            ("p95", 0.95),
                            ("p99", 0.99),
                        )
                    },
                }
                if durations
                else None
            ),
            "fatal_events": [r for r in records if r.get("event") == "fatal"],
            "exit_resources": [r for r in records if r.get("event") == "resources"],
        }
    result["workload"] = operations
    if (directory / "replicas.json").exists():
        replicas = json.loads((directory / "replicas.json").read_text())
        result["storage"] = {k: v for k, v in replicas.items() if k != "copies"}
        result["storage"].update(
            {
                "body_compression_ratio": 1.0,
                "replication_factor": 3,
                "checksum_metadata_included": False,
            }
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
