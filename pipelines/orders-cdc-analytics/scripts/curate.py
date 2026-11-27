"""Compact evidence for this lab; raw logs, histories and data remain ignored."""

import hashlib
import json
import re
import subprocess
import sys

import lab


def latest(mode):
    paths = sorted((lab.ROOT / "results").glob(mode + "-*/status.json"))
    run = lab.fixture()["id"]
    paths = [
        path
        for path in paths
        if (path.parent / "fixture.json").exists()
        and json.loads((path.parent / "fixture.json").read_text())["id"] == run
    ]
    if not paths:
        raise RuntimeError("No executed evidence for " + mode)
    path = paths[-1].parent
    if not json.loads((path / "status.json").read_text()).get("passed"):
        raise RuntimeError("Latest case failed: " + str(path))
    return path


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def compact_report(result):
    config = result.pop("configuration")
    result["deployment"] = {
        "effective_compose_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True).encode()
        ).hexdigest(),
        "services": {
            name: {
                key: row.get(key) for key in ("image", "cpus", "mem_limit", "deploy")
            }
            for name, row in config["services"].items()
        },
        "source": "Tracked docker-compose.yml; raw effective configuration remains in ignored evidence",
    }
    storage = result["phases"]["storage"]
    storage["volumes"] = [
        row for row in storage["volumes"] if row.get("event") == "volume_sizes"
    ]
    for row in storage["native_cgroups"].values():
        raw = row.pop("stdout")
        if row["exit_code"]:
            row["unavailable"] = row.pop("stderr")
            continue
        row.pop("stderr")
        if raw.startswith("{"):
            data = next(
                item
                for item in map(json.loads, raw.splitlines())
                if item.get("event") == "native_cgroup"
            )
        else:
            data = dict(section.split("\n", 1) for section in raw.split("METRIC:")[1:])
        row["memory_current_bytes"] = int(data["memory.current"])
        row["memory_peak_bytes"] = int(data["memory.peak"])
        row["cpu_stat"] = {
            key: int(value)
            for key, value in (line.split() for line in data["cpu.stat"].splitlines())
        }
        row["io_stat"] = {
            key: sum(
                int(value)
                for value in re.findall(r"\b" + key + r"=(\d+)", data["io.stat"])
            )
            for key in ("rbytes", "wbytes", "rios", "wios")
        }
    after = result["phases"]["worker-failure"]["result"]["after_checkpoints"]
    result["phases"]["worker-failure"]["result"]["after_checkpoints"] = {
        "counts": after["counts"],
        "latest_restored": after["latest"].get("restored"),
    }
    return result


def main():
    phases = {}
    for mode in (
        "dimension-test",
        "worker-failure",
        "delete-test",
        "verify-replicas",
        "routing",
        "benchmark",
    ):
        path = latest(mode)
        entry = {"evidence": str(path.relative_to(lab.ROOT)), "passed": True}
        for name in ("result.json", "topology-verdict.json", "checkpoint-config.json"):
            if (path / name).exists():
                entry[name.removesuffix(".json")] = json.loads(
                    (path / name).read_text()
                )
        entry["readbacks"] = [
            row
            for filename in (
                "verify.log",
                "replica-0.log",
                "replica-1.log",
                "replica-2.log",
            )
            if (path / filename).exists()
            for row in events(path / filename)
            if row.get("event") in ("verified", "receipts_verified", "resources")
        ]
        if mode == "worker-failure":
            writer, reader = events(path / "writer.log"), events(path / "reader.log")
            entry["live"] = {
                "source_acks": sum(row.get("event") == "ack" for row in writer),
                "source_ambiguous": sum(
                    row.get("event") == "ambiguous" for row in writer
                ),
                "historical_queries": sum(row.get("event") == "read" for row in reader),
                "reader_errors": sum(
                    row.get("event") in ("read_error", "fatal") for row in reader
                ),
                "native_fault": json.loads((path / "fault.json").read_text())["after"],
            }
        phases[mode] = entry
    resource_phases = {}
    for raw in sorted((lab.ROOT / "results").glob("resources-*.jsonl")):
        header = json.loads(raw.open().readline())
        if header.get("fixture") != lab.fixture()["id"]:
            continue
        if header.get("command") != ["make", "benchmark"] and header.get("command") != [
            "make",
            "worker-failure",
        ]:
            continue
        result = subprocess.check_output(
            [sys.executable, str(lab.ROOT / "scripts/resource_report.py"), str(raw)],
            text=True,
        )
        resource_phases[header["command"][-1]] = json.loads(result)
    if set(resource_phases) != {"benchmark", "worker-failure"}:
        raise RuntimeError(
            "Missing fixture-owned benchmark/fault resource observations"
        )
    resource_path = latest("resources")
    phases["storage"] = {
        "evidence": str(resource_path.relative_to(lab.ROOT)),
        "volumes": events(resource_path / "volumes.jsonl"),
        "native_cgroups": json.loads(
            (resource_path / "native-cgroups.json").read_text()
        ),
    }
    sources = {
        str(path.relative_to(lab.ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for directory in ("scripts", "sql", "docker", "orders-workload", "config")
        for path in sorted((lab.ROOT / directory).glob("*"))
        if path.is_file()
    }
    result = {
        "date": "2026-11-27",
        "performance_depth": 3,
        "phases": phases,
        "resource_phases": resource_phases,
        "current_source_sha256": sources,
        "configuration": json.loads(
            subprocess.check_output(
                [*lab.COMPOSE, "--profile", "tools", "config", "--format", "json"],
                cwd=lab.ROOT,
                text=True,
            )
        ),
        "limits": [
            "Current source hashes identify the curated checkout, not historical failed binaries; per-process logs preserve exact build manifests.",
            "Million-row trials used a 600-second checkpoint timeout/minimum pause 0; final SQL explicitly sets 120 seconds/1 second and requires a separate final-configuration lifecycle smoke test.",
            "Resource observations are sample-window scopes; native I/O snapshots are cumulative per-container and include background work. Do not add these scopes together.",
            "Native ZSTD/LZ4 codec ratios, physical I/O latency/queue depth and power-loss durability are unmeasured.",
            "Source CSV gzip reference is a different layer from native database/wire compression.",
            "No global exactly-once/linearizability proof or competitive capacity winner.",
        ],
    }
    (lab.ROOT / "reports/QUALIFICATION-2026-11-27.json").write_text(
        json.dumps(compact_report(result), indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
