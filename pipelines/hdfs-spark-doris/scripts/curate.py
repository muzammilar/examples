"""Compact fixture-scoped good/bad evidence; never select an older passing phase."""

import hashlib
import json
from pathlib import Path
import lab
from resource_report import summarize

REQUIRED = (
    "docker-up",
    "up",
    "test",
    "executor-failure",
    "datanode-failure",
    "benchmark",
    "down",
    "docker-down",
)


def validate_phase(mode, path, status, latest):
    if mode == "datanode-failure" and not status["passed"]:
        repair, repair_status = latest.get("observe-repair", (None, {}))
        if (
            not repair_status.get("passed")
            or json.loads((repair / "automatic-repair.json").read_text())["failed_case"]
            != path.name
        ):
            raise ValueError("Failed DataNode case requires recovery of that exact run")
    elif not status["passed"]:
        raise ValueError("Newest required phase failed: " + path.name)


def compact_resources(raw):
    """Keep role totals; per-actor counters remain in the ignored raw logs."""
    groups = {}
    for actor in raw.pop("actor_lifetime_counters"):
        name = actor["log"]
        role = (
            "parquet-verifier"
            if "parquet-verifier" in name
            else ("orders" if "orders" in name or name == "driver.log" else "oracle")
        )
        group = groups.setdefault(
            role, {"actors": 0, "counters": {}, "missing_metrics": []}
        )
        group["actors"] += 1
        for key in ("cpu_us", "memory.peak", "rbytes", "wbytes", "rios", "wios"):
            if key not in actor["counters"]:
                group["missing_metrics"].append({"log": name, "metric": key})
                continue
            value = actor["counters"][key]
            if key == "memory.peak":
                group["counters"][key] = max(group["counters"].get(key, 0), value)
            else:
                group["counters"][key] = group["counters"].get(key, 0) + value
    raw["actor_role_totals"] = groups
    raw["actor_peak_scope"] = (
        "Maximum individual actor memory.peak per role, not simultaneous aggregate memory"
    )
    return raw


def main():
    if (lab.ROOT / "results/fixture.json").exists():
        raise ValueError(
            "Active fixture must be preserved and closed before final curation"
        )
    destroyed = sorted(
        (lab.ROOT / "results").glob("docker-down-*/destroyed-fixture.json")
    )
    if not destroyed:
        raise ValueError("Full owned teardown receipt required")
    fixture = json.loads(destroyed[-1].read_text())
    latest, failures = {}, []
    for path in sorted((lab.ROOT / "results").iterdir()):
        if not path.is_dir() or not (path / "fixture.json").exists():
            continue
        if json.loads((path / "fixture.json").read_text())["id"] != fixture["id"]:
            continue
        mode = path.name.rsplit("-", 1)[0]
        status = json.loads((path / "status.json").read_text())
        latest[mode] = path, status
        if not status["passed"]:
            failures.append(dict(phase=path.name, status=status))
    phases = {}
    required = (*REQUIRED, "observe-repair") if "observe-repair" in latest else REQUIRED
    for mode in required:
        path, status = latest[mode]
        validate_phase(mode, path, status, latest)
        phase = dict(
            raw_path=str(path.relative_to(lab.ROOT)),
            status=status,
            resources=compact_resources(summarize(path)),
        )
        for filename in (
            "topology-verdict.json",
            "recovery.json",
            "volumes.json",
            "verified-batches.json",
            "automatic-repair.json",
        ):
            if (path / filename).exists():
                value = json.loads((path / filename).read_text())
                if filename == "recovery.json":
                    value["failed_tasks"] = [
                        {
                            key: task.get(key)
                            for key in (
                                "Stage ID",
                                "Stage Attempt ID",
                                "Task End Reason",
                            )
                        }
                        for task in value["failed_tasks"]
                    ]
                phase[filename] = value
        results = []
        for log in sorted(path.glob("*.log")):
            for line in log.read_text(errors="replace").splitlines():
                text = line.removeprefix("PIPELINE_RESULT ")
                if not text.startswith("{"):
                    continue
                try:
                    row = json.loads(text)
                except ValueError:
                    continue
                if "client_config" in row:
                    phase["client_reference"] = {
                        key: value
                        for key, value in row["client_config"].items()
                        if key != "batch"
                    }
                if row.get("query") == "SELECT @@version_comment":
                    phase["native_engine_version"] = row["rows"]
                if row.get("query") == "SHOW LOAD FROM pipeline":
                    receipts = []
                    for receipt in row["rows"]:
                        details = json.loads(receipt.get("JobDetails", "{}"))
                        receipts.append(
                            {
                                **{
                                    key: receipt.get(key)
                                    for key in (
                                        "Label",
                                        "State",
                                        "TransactionId",
                                        "Type",
                                        "CreateTime",
                                        "LoadFinishTime",
                                    )
                                },
                                "details": {
                                    key: details.get(key)
                                    for key in (
                                        "ScannedRows",
                                        "LoadBytes",
                                        "FileNumber",
                                        "FileSize",
                                        "FilteredRows",
                                    )
                                },
                            }
                        )
                    phase["native_load_receipts"] = receipts
                if "parquet_column_compressed_bytes" in row:
                    row["parquet_file_count"] = len(row.pop("files"))
                if row.get("mode") in ("intent", "retained-label"):
                    continue
                if (
                    "measured" in row
                    or "parquet_column_compressed_bytes" in row
                    or "mode" in row
                ):
                    results.append(row)

        phase["results"] = results
        phases[mode] = phase
    runtime = {}
    for mode in ("docker-up", "up"):
        path = latest[mode][0]
        for file in sorted(path.glob("*-inspect.json")):
            value = json.loads(file.read_text())
            for container in value if isinstance(value, list) else [value]:
                role = container["Config"]["Labels"]["com.docker.compose.service"]
                if role in runtime:
                    continue
                runtime[role] = {
                    "image": container["Config"]["Image"],
                    "local_image_config_id": container["Image"],
                    "effective_cpu_cap": container["HostConfig"]["NanoCpus"] / 1e9,
                    "effective_memory_bytes": container["HostConfig"]["Memory"],
                }
    source = {}
    for directory in (
        "scripts",
        "orders-workload",
        "serving-oracle",
        "docker",
        "deploy",
    ):
        for file in sorted((lab.ROOT / directory).rglob("*")):
            if file.is_file() and "__pycache__" not in file.parts:
                source[str(file.relative_to(lab.ROOT))] = hashlib.sha256(
                    file.read_bytes()
                ).hexdigest()
    for name in ("Makefile", "compose.yaml"):
        source[name] = hashlib.sha256((lab.ROOT / name).read_bytes()).hexdigest()
    supplements = {}
    for file in (
        lab.ROOT / ("results/budget-correction-" + fixture["id"] + ".json"),
        lab.ROOT / "results/native-repair-evidence.json",
    ):
        if file.exists():
            value = json.loads(file.read_text())
            if value["fixture_id"] != fixture["id"]:
                raise ValueError("Foreign evidence supplement")
            supplements[file.name] = value
    result = dict(
        verdict={
            "healthy_correctness": "passed",
            "worker_recovery": "passed",
            "datanode_180s_redundancy_deadline": (
                "passed" if latest["datanode-failure"][1]["passed"] else "failed"
            ),
            "eventual_automatic_repair": (
                "passed separately" if "observe-repair" in latest else "not required"
            ),
            "performance": "diagnostics only; no qualified competitive winner",
        },
        fixture=fixture,
        supplements=supplements,
        effective_runtime_roles=runtime,
        source_sha256=source,
        phases=phases,
        preserved_failed_phases=failures,
        limitations=[
            "One Docker VM and disk; no physical host/AZ fault claim.",
            "One NameNode/master/driver; metadata and driver HA unqualified.",
            "Separate atomic table loads, not cross-table atomic publication.",
            "Finite label retention; no permanent deduplication claim.",
            "Parquet codec CPU not isolated from verification.",
            "No physical cold-cache or capacity/comparative winner claim.",
        ],
    )
    lab.save(lab.ROOT / "reports/QUALIFICATION.json", result)
    print(lab.ROOT / "reports/QUALIFICATION.json")


if __name__ == "__main__":
    main()
