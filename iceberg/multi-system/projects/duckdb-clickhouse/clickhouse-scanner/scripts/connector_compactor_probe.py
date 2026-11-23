"""Run a read-only native maintenance compatibility gate on one preserved fixture."""

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import time
import uuid

import cold_readback
import cold_storage
import compactor_control
import duckdb_dependencies
import duckdb_maintenance
import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler


def source(identifier):
    run_id = uuid.UUID(identifier)
    if str(run_id) != identifier:
        raise ValueError("canonical source UUID required")
    case = cleanup.regular(
        cleanup.results_root() / "duckdb-connector-dependencies" / identifier,
        cleanup.results_root(),
    )
    result = cleanup.load(case / "result.json", case)
    invocation = cleanup.load(case / "invocation.json", case)
    catalog = cleanup.load(case / "probe-catalog-final.json", case)["metadata"]
    if (
        result.get("passed") is not True
        or invocation.get("table") != "duck_compact_" + run_id.hex
        or result["checks"].get("recovery_probe_rows_verified") != 3000
    ):
        raise RuntimeError("require a passed native 3k probe fixture")
    first = cleanup.load(case / "cold-input.json", case)["append_first"]
    if (
        type(first) is not int
        or not 0 <= first < 10000000
        or type(catalog["current-snapshot-id"]) is not int
        or catalog["current-snapshot-id"] <= 0
    ):
        raise RuntimeError("bounded native IDs required")
    return {
        "source_case": identifier,
        "table": invocation["table"],
        "table_uuid": catalog["table-uuid"],
        "snapshot_id": catalog["current-snapshot-id"],
        "first_id": first,
        "rows": 3000,
    }


def run(identifier):
    if control.lab.PROFILE != "current":
        raise RuntimeError("current profile required")
    context = control.local_docker()
    cold_readback.original_plane()
    fixture = source(identifier)
    root = cleanup.results_root() / "connector-compactor-probes" / str(uuid.uuid4())
    root.mkdir(parents=True)
    storage, query = control.service("storage"), control.service("clickhouse")
    before = duckdb_dependencies.catalog(storage, fixture["table"])
    if (
        before["metadata"]["table-uuid"],
        before["metadata"].get("current-snapshot-id"),
    ) != (fixture["table_uuid"], fixture["snapshot_id"]):
        raise RuntimeError("fixture changed; inspect its new history before reuse")
    control.save(root / "input.json", fixture)
    control.save(root / "catalog-before.json", before)
    frozen = control.sources()
    for relative, sha in frozen.items():
        path = root / "source" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (control.ROOT / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise RuntimeError("source changed before freeze")
        control.save_text(path, data.decode())
    control.save_text(
        root / "spark_fixture_probe.py",
        (control.ROOT / "projects/duckdb-clickhouse/clickhouse-scanner/scripts/connector_compactor_job.py").read_text(),
    )
    image = json.loads(
        control.call(
            ["docker", "image", "inspect", "iceberg-multi-compactor-current:dev"]
        )
    )[0]
    if image["Config"].get("Labels", {}).get("org.examples.owner") != control.OWNER:
        raise RuntimeError("unowned maintenance image")
    name = "iceberg-connector-compactor-" + root.name.replace("-", "")
    control.save(
        root / "invocation.json",
        {
            "context": context,
            "image_id": image["Id"],
            "name": name,
            "source_sha256": frozen,
            "started_ns": time.time_ns(),
        },
    )
    sampler = ResourceSampler(root, env=control.DOCKER_ENV)
    sampler.bind("storage", storage["Id"], "/data")
    sampler.bind("clickhouse", query["Id"], "/var/lib/clickhouse")
    sampler.start()
    failure, result = None, None

    def planned():
        ids = control.call(
            [
                "docker",
                "ps",
                "--all",
                "--no-trunc",
                "-q",
                "--filter",
                "name=^/" + name + "$",
            ]
        ).split()
        if not ids:
            return None
        if len(ids) != 1:
            raise RuntimeError("ambiguous probe owner")
        value = control.owned(ids[0], "recovery-compactor")
        if value["Name"] != "/" + name or value["Image"] != image["Id"]:
            raise RuntimeError("maintenance probe identity differs")
        return value

    try:
        if planned() is not None:
            raise RuntimeError("probe name already exists")
        env = control.DOCKER_ENV | {"RESULTS_DIR": str(root.relative_to(control.ROOT))}
        command = control.COMPOSE + [
            "run",
            "-d",
            "--no-deps",
            "--name",
            name,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--entrypoint",
            "/bin/sh",
            "-e",
            "COMPACTOR_S3_ENDPOINT=http://storage:8333",
            "recovery-compactor",
            "-c",
            "while ! test -f /results/release; do sleep .1; done; exec /opt/spark/bin/spark-submit --master local[2] --driver-memory 1g --conf spark.jars.ivy=/tmp/ivy --conf spark.driver.extraJavaOptions=-Duser.home=/tmp /results/spark_fixture_probe.py",
        ]
        actual = control.call(command, env=env).strip()
        value = planned()
        if value is None or value["Id"] != actual:
            raise RuntimeError("created probe cannot be identified")
        control.check_caps(value, "recovery-compactor")
        control.check_mounts(value, root)
        control.check_networks(
            value,
            {n["NetworkID"] for n in storage["NetworkSettings"]["Networks"].values()},
        )
        compactor_control.helper_sources(
            value, "recovery-compactor", {"source_sha256": frozen}
        )
        control.save(root / "started.json", control.state(value))
        sampler.bind("spark-probe", actual, "/results")
        control.wait_coverage(
            sampler, ["storage", "clickhouse", "spark-probe"], time.time_ns()
        )
        control.save_text(root / "release", "release\n")
        deadline = time.monotonic() + 120
        while planned()["State"]["Running"]:
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "read-only native maintenance probe exceeded deadline"
                )
            time.sleep(0.2)
        value = planned()
        control.save(root / "ended.json", control.state(value))
        logs = subprocess.run(
            ["docker", "logs", actual],
            env=control.DOCKER_ENV,
            text=True,
            capture_output=True,
            timeout=20,
        )
        control.save_text(root / "native-stdout.log", logs.stdout)
        control.save_text(root / "native-stderr.log", logs.stderr)
        if logs.returncode:
            raise RuntimeError("native logs unavailable")
        after = duckdb_dependencies.catalog(storage, fixture["table"])
        control.save(root / "catalog-after.json", after)
        if after["metadata"]["current-snapshot-id"] != fixture["snapshot_id"]:
            raise RuntimeError("read-only probe changed the published snapshot")
        result = cleanup.load(root / "job-result.json", root)
        if value["State"]["ExitCode"] != 0 or result.get("passed") is not True:
            raise RuntimeError("native read-only maintenance compatibility failed")
        if control.sources() != frozen:
            raise RuntimeError("probe source changed during execution")
    except BaseException as error:
        failure = error
    finally:
        errors = duckdb_maintenance.finish_cli(root, planned, sampler, (storage, query))
        if errors:
            failure = failure or RuntimeError(repr(errors))
        control.save(
            root / "result.json",
            {
                "passed": failure is None,
                "error": repr(failure) if failure else None,
                "native_result": result,
                "cleanup_errors": errors,
                "completed_ns": time.time_ns(),
                "scope": "Read-only 3,000-row Spark compatibility gate only; million-row concurrent compaction remains required.",
            },
        )
    print(root.relative_to(control.ROOT), flush=True)
    if failure:
        raise failure


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_case")
    args = parser.parse_args()
    with cold_storage.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.source_case)
