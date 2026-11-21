"""Publish one explicit, finalized journal correctness run with resource evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics

import experiment as bench
import ingestion


def load(path):
    return json.loads(path.read_text())


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "journal-live").resolve()):
        raise ValueError("select an owned journal-live result directory")
    result, setup, costs = [
        load(root / name) for name in ("result.json", "setup.json", "costs.json")
    ]
    if (
        not result["passed"]
        or not result.get("finalized_after_cleanup")
        or costs["cleanup_errors"]
    ):
        raise RuntimeError("run/cleanup did not pass")
    for name, digest in setup["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("fixture changed: " + name)
    scenario = setup.get("scenario", "process-crash")
    fencing = None
    if scenario == "writer-lock":
        fencing = load(root / "fencing-proof.json")
        if (
            not fencing["passed"]
            or len(fencing["attempts"]) != 10
            or not all(a["rejected_by_lock"] for a in fencing["attempts"])
        ):
            raise RuntimeError("missing native lock rejection proof")
    elif scenario == "process-crash":
        for cycle in (1, 2):
            killed = load(root / f"cycle-{cycle}-killed.json")
            if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
                raise RuntimeError("missing kill proof")
    else:
        raise ValueError("unknown journal scenario")
    samples = [
        json.loads(line) for line in (root / "resources.jsonl").read_text().splitlines()
    ]
    native = {}
    images = set()
    for path in root.glob("*-inspect.json"):
        if path.name == "contender-inspect.json":
            continue
        inspections = load(path)
        for role, info in inspections.items():
            images.add(info["Image"])
            expected_memory = (4 * 1024**3) if role == "server" else (512 * 1024**2)
            if (
                info["HostConfig"]["Memory"] != expected_memory
                or info["HostConfig"]["MemorySwap"] != expected_memory
                or info["HostConfig"]["NanoCpus"] != 10**9
            ):
                raise RuntimeError("effective resource budget differs: " + role)
            text = (
                root
                / (path.name.removesuffix("-inspect.json") + "-" + role + "-native.txt")
            ).read_text()
            generation = info["State"]["StartedAt"]
            entry = native.setdefault(role, {}).setdefault(
                generation,
                {
                    "cpu_core_seconds": 0,
                    "user_core_seconds": 0,
                    "system_core_seconds": 0,
                    "cgroup_peak_bytes": 0,
                    "process_peak_rss_bytes": 0,
                    "io_checkpoints": {},
                },
            )
            for field, output in (
                ("usage_usec", "cpu_core_seconds"),
                ("user_usec", "user_core_seconds"),
                ("system_usec", "system_core_seconds"),
            ):
                entry[output] = max(
                    entry[output],
                    int(re.search(r"^" + field + r" (\d+)$", text, re.M)[1]) / 10**6,
                )
            entry["cgroup_peak_bytes"] = max(
                entry["cgroup_peak_bytes"],
                int(text.split("__MEMORY__\n")[1].splitlines()[0]),
            )
            entry["process_peak_rss_bytes"] = max(
                entry["process_peak_rss_bytes"],
                int(re.search(r"^VmHWM:\s+(\d+) kB$", text, re.M)[1]) * 1024,
            )
            io = bench.io_counters(text.split("__IO__\n")[1], 1)
            # No one-second observation was made; only cumulative values are used.
            io.pop("per_second")
            entry["io_checkpoints"][path.name] = io
    if len(images) != 1:
        raise RuntimeError("actor/server images differ")
    phases = {}
    previous = {}
    disk_max = 0
    for snapshot in samples:
        disk_max = max(
            disk_max,
            sum(
                v["bytes"]
                for k, v in snapshot["disk"]["files"].items()
                if k.startswith("journal")
            ),
        )
        for role, observation in snapshot["roles"].items():
            value = observation.get("sample")
            if not value:
                continue
            cell = phases.setdefault(snapshot["phase"], {}).setdefault(
                role,
                {
                    "cpu_observed_core_seconds": 0,
                    "cpu_observed_window_s": 0,
                    "cpu_utilization_peak_percent_one_core_100": 0,
                    "memory_samples": [],
                    "network_bytes_at_last_sample": None,
                },
            )
            cell["memory_samples"].append(value["charged_memory_bytes"])
            cell["network_bytes_at_last_sample"] = value["network_bytes"]
            before = previous.get(role)
            if (
                before
                and before[0] == snapshot["phase"]
                and before[1] == observation["started_at"]
            ):
                elapsed = value["time"] - before[2]["time"]
                cpu = (value["cpu_ns"] - before[2]["cpu_ns"]) / 1e9
                if elapsed <= 0 or cpu < 0:
                    raise RuntimeError("resource counters moved backwards")
                cell["cpu_observed_core_seconds"] += cpu
                cell["cpu_observed_window_s"] += elapsed
                cell["cpu_utilization_peak_percent_one_core_100"] = max(
                    cell["cpu_utilization_peak_percent_one_core_100"],
                    100 * cpu / elapsed,
                )
            previous[role] = (snapshot["phase"], observation["started_at"], value)
    for roles in phases.values():
        for cell in roles.values():
            memory = cell.pop("memory_samples")
            cell.update(
                sample_count=len(memory),
                sampled_memory_mean_bytes=statistics.mean(memory),
                sampled_memory_max_bytes=max(memory),
            )
            cell["cpu_utilization_mean_percent_one_core_100"] = (
                100 * cell["cpu_observed_core_seconds"] / cell["cpu_observed_window_s"]
                if cell["cpu_observed_window_s"]
                else None
            )
    events = [
        json.loads(line) for line in (root / "history.jsonl").read_text().splitlines()
    ]
    completion_rates = {}
    for phase in {e["phase"] for e in events if e["kind"] in {"read", "write"}}:
        for kind in ("read", "write"):
            operations = [
                e for e in events if e["kind"] == kind and e["phase"] == phase
            ]
            if not operations:
                continue
            duration = (
                max(e["monotonic_ns"] for e in operations)
                - min(e["invocation_ns"] for e in operations)
            ) / 1e9
            completion_rates[phase + "/" + kind] = {
                "observed_window_s": duration,
                "successful_per_second": sum(e["status"] == "ok" for e in operations)
                / duration,
                "failed_per_second": sum(e["status"] != "ok" for e in operations)
                / duration,
                "scope": "First invocation to last fsynced completion for this actor/phase; closed-loop offered load, not capacity",
            }
    volume_init = (
        load(root / "volume-initialization.json")
        if setup.get("storage") == "volume"
        else None
    )
    if volume_init is not None:
        if (
            not (root / "volume-export.json").is_file()
            or load(root / "volume-export-fenced.json")["State"]["Running"]
        ):
            raise RuntimeError("native volume was not fenced/exported")
    if fencing and fencing["image_id"] not in images:
        raise RuntimeError("contender used another image")
    report = {
        "source_directory": str(root.relative_to(bench.ROOT)),
        "setup": setup,
        "result": result,
        "costs": costs,
        "fencing": fencing,
        "volume_initialization": volume_init,
        "image_id": next(iter(images)),
        "native_generation_counters": native,
        "phase_sample_summaries": phases,
        "observed_completion_rates": completion_rates,
        "history_sha256": hashlib.sha256(
            (root / "history.jsonl").read_bytes()
        ).hexdigest(),
        "resource_history_sha256": hashlib.sha256(
            (root / "resources.jsonl").read_bytes()
        ).hexdigest(),
        "sampled_native_file_bytes_max": disk_max,
        "scope": "One synchronous Rayforce declared-scenario correctness experiment, not a DuckDB comparison or capacity benchmark. Native checkpoint counters are observed lower bounds on full lifetime CPU/I/O costs and observed memory peaks. Checkpoint exec overhead is included. Phase sample intervals crossing phase/generation boundaries are excluded. Memory means are sample means. Network values are cumulative generation counters, not per-phase traffic. Missing I/O/device latency and physical host I/O remain unavailable.",
        "unrun": [
            "equivalent DuckDB live adapter/recovery",
            "asynchronous journal and explicit sync watermark",
            "interrupted snapshot publication",
            "torn tails and interior corruption",
            "online backup/restore availability",
            "bounded disk/CPU/memory pressure",
            "repeated sustained capacity and verified OS-cold matrices",
        ],
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    ingestion.save(
        bench.ROOT
        / (
            "FENCING_RESULTS.json"
            if scenario == "writer-lock"
            else "JOURNAL_VOLUME_RESULTS.json"
            if setup.get("storage") == "volume"
            else "JOURNAL_RESULTS.json"
        ),
        report,
    )
    print("Published finalized synchronous journal correctness and resource evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_directory", type=Path)
    publish(parser.parse_args().result_directory)
