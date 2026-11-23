"""Validate one explicit completed native recovery run and publish compact evidence."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import statistics

import experiment as bench
import ingestion
from live_journal import reconcile
from journal_restore import fingerprint


def load(path):
    return json.loads(path.read_text())


def counters(text, elapsed):
    if elapsed <= 0:
        raise ValueError("resource observation window must be positive")
    result = {
        "cpu_core_seconds": int(re.search(r"^usage_usec (\d+)$", text, re.M)[1]) / 1e6,
        "user_core_seconds": int(re.search(r"^user_usec (\d+)$", text, re.M)[1]) / 1e6,
        "system_core_seconds": int(re.search(r"^system_usec (\d+)$", text, re.M)[1])
        / 1e6,
        "process_peak_rss_bytes": int(re.search(r"^VmHWM:\s+(\d+) kB$", text, re.M)[1])
        * 1024,
        "cgroup_peak_bytes": int(text.split("__MEMORY__\n")[1].splitlines()[0]),
        "memory_events": dict(
            line.split()
            for line in text.split("__MEMORY_EVENTS__\n")[1]
            .split("__IO__")[0]
            .strip()
            .splitlines()
        ),
        "io": bench.io_counters(text.split("__IO__\n")[1], elapsed),
        "observed_window_s": elapsed,
    }
    result["cpu_utilization_percent_one_core_100"] = (
        100 * result["cpu_core_seconds"] / elapsed
    )
    return result


def publish(root):
    root = root.resolve(strict=True)
    if not root.is_relative_to((bench.RESULTS / "duckdb-live").resolve()):
        raise ValueError("select an owned duckdb-live result directory")
    result, setup, controller = (
        load(root / name) for name in ("result.json", "setup.json", "controller.json")
    )
    if (
        not result["passed"]
        or controller["cleanup_errors"]
        or (root / "failure.json").exists()
    ):
        raise RuntimeError("recovery/cleanup did not pass")
    for name, digest in setup["source_sha256"].items():
        if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("tested source differs: " + name)
    for name, digest in setup["fixture"]["sha256"].items():
        with (bench.RESULTS / "data" / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("fixture differs: " + name)
    events = [
        json.loads(line) for line in (root / "history.jsonl").read_text().splitlines()
    ]
    if reconcile(
        bench.RESULTS / "data/trades.csv", root / "state/recovered.csv", events
    ) != {key: value for key, value in result["proof"].items() if key != "customers"}:
        raise RuntimeError("replayed persisted witness proof differs")
    if (root / "state/customers.csv").read_bytes() != (
        bench.RESULTS / "data/customers.csv"
    ).read_bytes():
        raise RuntimeError("persisted customer bodies differ")
    files = sorted(
        p
        for p in (root / "state").iterdir()
        if p.is_file() and p.name.startswith("events.duckdb")
    )
    if fingerprint(files) != result["native_files"]:
        raise RuntimeError("native exported file hashes differ")
    for cycle in (1, 2):
        killed = load(root / f"cycle-{cycle}-killed.json")
        if killed["State"]["Running"] or killed["State"]["ExitCode"] != 137:
            raise RuntimeError("crash not established")
    stopped = load(root / "server-stopped.json")
    if stopped["State"]["Running"] or stopped["State"]["ExitCode"]:
        raise RuntimeError("final graceful close failed")
    images, identities, native = set(), {}, {}
    for path in sorted(root.glob("*-inspection.json")):
        info = load(path)
        role = info["Config"]["Labels"]["com.docker.compose.service"]
        role = "server" if role == "database" else info["Name"].split("-")[1]
        if role not in {"server", "writer", "reader", "control"}:
            raise RuntimeError("unknown native resource role")
        budget = (4 * 1024**3) if role == "server" else 512 * 1024**2
        if (
            any(
                info["HostConfig"][field] != budget
                for field in ("Memory", "MemorySwap")
            )
            or info["HostConfig"]["NanoCpus"] != 10**9
        ):
            raise RuntimeError("effective resource budget differs")
        images.add(info["Image"])
        generation = info["State"]["StartedAt"]
        identities.setdefault(role, set()).add((info["Id"], generation))
        observation = load(
            path.with_name(path.name.replace("-inspection.json", "-native.json"))
        )
        text = "__PROCESS__\n" + observation["process_status"]
        for key, tag in (
            ("cpu.stat", "CPU"),
            ("memory.peak", "MEMORY"),
            ("memory.events", "MEMORY_EVENTS"),
            ("io.stat", "IO"),
        ):
            text += "\n__" + tag + "__\n" + observation["cgroup"][key]
        native.setdefault(role, {})[path.name] = {
            "generation": generation,
            "counters": counters(
                text,
                observation["observed_unix_ns"] / 1e9
                - datetime.fromisoformat(generation.replace("Z", "+00:00")).timestamp(),
            ),
            "scope": "pre-stop checkpoint, lower bound on generation lifetime",
        }
    if (
        len(images) != 1
        or len(identities.get("server", [])) != 3
        or any(
            len(identities.get(role, [])) != 1
            for role in ("writer", "reader", "control")
        )
    ):
        raise RuntimeError(
            "expected three server generations and unchanged independent sessions"
        )
    for role in ("server", "writer", "reader", "control"):
        path = root / ("server-final.log" if role == "server" else role + ".stderr")
        matches = re.findall(r"^__DUCKDB_FINAL__([0-9a-f]+)$", path.read_text(), re.M)
        if len(matches) != 1:
            raise RuntimeError("missing post-close native resource counters: " + role)
        text = bytes.fromhex(matches[0]).decode()
        elapsed = int(text.split("__ELAPSED_NS__\n")[1].splitlines()[0]) / 1e9
        native[role]["final"] = {
            "counters": counters(text, elapsed),
            "scope": "after native handles close; server only final generation, killed generations remain lower bounds",
        }
    samples = [
        json.loads(line) for line in (root / "resources.jsonl").read_text().splitlines()
    ]
    cells, previous, disk = {}, {}, []
    for snapshot in samples:
        storage = snapshot.get("disk", {})
        if "files" in storage:
            disk.append(
                sum(
                    value["bytes"]
                    for name, value in storage["files"].items()
                    if name.startswith("events.duckdb")
                )
            )
        for role, observation in snapshot["roles"].items():
            value = observation.get("sample")
            if not value:
                continue
            cell = cells.setdefault(snapshot["phase"], {}).setdefault(
                role,
                {
                    "memory": [],
                    "cpu_core_seconds": 0,
                    "window_s": 0,
                    "cpu_peak_percent_one_core_100": None,
                },
            )
            cell["memory"].append(value["charged_memory_bytes"])
            before = previous.get(role)
            if before and before[:2] == (snapshot["phase"], observation["started_at"]):
                elapsed = value["time"] - before[2]["time"]
                cpu = (value["cpu_ns"] - before[2]["cpu_ns"]) / 1e9
                if elapsed <= 0 or cpu < 0:
                    raise RuntimeError("sample counters moved backwards")
                cell["cpu_core_seconds"] += cpu
                cell["window_s"] += elapsed
                peak = cell["cpu_peak_percent_one_core_100"]
                cell["cpu_peak_percent_one_core_100"] = max(
                    peak or 0, 100 * cpu / elapsed
                )
            previous[role] = (snapshot["phase"], observation["started_at"], value)
            cell["network_bytes_last_sample"] = value["network_bytes"]
    for roles in cells.values():
        for cell in roles.values():
            memory = cell.pop("memory")
            cell.update(
                memory_samples=len(memory),
                memory_mean_bytes=statistics.mean(memory),
                memory_max_bytes=max(memory),
                cpu_mean_percent_one_core_100=100
                * cell["cpu_core_seconds"]
                / cell["window_s"]
                if cell["window_s"]
                else None,
            )
    rates = {}
    for phase in {
        event["phase"] for event in events if event["kind"] in {"read", "write"}
    }:
        for kind in ("read", "write"):
            operations = [
                event
                for event in events
                if event["kind"] == kind and event["phase"] == phase
            ]
            if not operations:
                continue
            duration = (
                max(e["monotonic_ns"] for e in operations)
                - min(e["invocation_ns"] for e in operations)
            ) / 1e9
            rates[phase + "/" + kind] = {
                "window_s": duration,
                "attempted": len(operations),
                "ok_per_s": sum(e["status"] == "ok" for e in operations) / duration,
                "error_per_s": sum(e["status"] != "ok" for e in operations) / duration,
            }
    report = {
        "source_directory": str(root.relative_to(bench.ROOT)),
        "setup": setup,
        "result": result,
        "controller": controller,
        "native_storage_evidence": [
            event
            for event in events
            if event["kind"] == "control"
            and (
                "pragma_storage_info" in event["source"]
                or "current_setting" in event["source"]
            )
        ],
        "initialization": load(root / "initialization.json"),
        "image_id": next(iter(images)),
        "native_resources": native,
        "phase_sample_summaries": cells,
        "operation_rates": rates,
        "sampled_native_file_bytes_max": max(disk),
        "history_sha256": hashlib.sha256(
            (root / "history.jsonl").read_bytes()
        ).hexdigest(),
        "resource_history_sha256": hashlib.sha256(
            (root / "resources.jsonl").read_bytes()
        ).hexdigest(),
        "limits": [
            "one functional trial, no performance ranking",
            "checkpoint/resource-exec overhead included",
            "killed-generation CPU/I/O snapshots are lower bounds; final generation has post-close counters",
            "sample CPU deltas exclude phase/generation transitions; memory is sample mean",
            "network counters cumulative, not per-phase deltas",
            "block I/O is Linux VM/cgroup, not physical host device I/O",
            "device latency/queue depth and host physical writes unavailable",
            "historical-resident reads are not OS-cold",
        ],
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    ingestion.save(bench.ROOT / "projects/duckdb-recovery/reports/DUCKDB_RECOVERY_RESULTS.json", report)
    print("Published verified DuckDB live recovery evidence:", root)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("result_directory", type=Path)
    publish(parser.parse_args().result_directory)
