"""Reconcile one witnessed table on a separate cold restore, retaining failed verdicts."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import cold_storage as cold
import recovery_cleanup as cleanup
import recovery_control as control
from recovery_resources import ResourceSampler

TEMPLATE = "deploy/compose/docker-compose.cold-readback.yml"
BASELINE_453_IMAGE = (
    "sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0"
)
UPGRADE_454_IMAGE = (
    "sha256:4b42c212543a0acd4c0e675215ecdcaf62a186ef98def76c7e12701871222d15"
)
DDL = """CREATE DATABASE lake ENGINE=DataLakeCatalog('http://storage:8181', 'demo', 'demo')
SETTINGS catalog_type='rest', catalog_credential='demo:demo', vended_credentials=false,
warehouse='warehouse', storage_endpoint='http://storage:8333/warehouse'"""


def owned_path(value):
    path = Path(value)
    if not path.is_absolute():
        path = control.ROOT / path
    return cleanup.regular(path, cleanup.results_root())


def checkpoint(path):
    path = owned_path(path)
    result = cleanup.load(path / "result.json", path)
    invocation = cleanup.load(path / "invocation.json", path)
    restored = cleanup.load(path / "restore-volume.json", path)
    archive = cleanup.load(path / "archive-proof.json", path)
    identifier = cleanup.valid_uuid(path.name)
    name = "iceberg-cold-" + uuid.UUID(identifier).hex
    if (
        result["passed"] is not True
        or result["run_id"] != identifier
        or invocation["run_id"] != identifier
        or result["restore_volume"] != name
        or invocation["restore_volume"] != name
        or restored["Name"] != name
        or restored.get("Labels", {}).get(cold.LABEL) != identifier
        or name == invocation["source_volume"]["Name"]
        or archive["sha256"] != cleanup.digest(path / "storage.tar.gz")
        or archive["bytes"] != (path / "storage.tar.gz").stat().st_size
        or archive["files_manifest_sha256"] != cleanup.digest(path / "files.sha256")
        or archive["metadata_manifest_sha256"] != cleanup.digest(path / "metadata.txt")
        or (path / "files.sha256").read_bytes()
        != cleanup.regular(path / "restored-files.sha256", path).read_bytes()
        or (path / "metadata.txt").read_bytes()
        != cleanup.regular(path / "restored-metadata.txt", path).read_bytes()
    ):
        raise RuntimeError("checkpoint identity, archive or filesystem restore differs")
    return path, invocation, restored


def fixture(case, verification, source_volume):
    case = owned_path(case)
    registered = {
        p.resolve(): m for group in cleanup.cases_by_volume().values() for p, m in group
    }
    metadata = registered.get(case.resolve())
    if metadata is None or cleanup.volume_key(metadata["volume"]) != cleanup.volume_key(
        source_volume
    ):
        raise RuntimeError("witness is not registered on the checkpoint source volume")
    identifier = cleanup.valid_uuid(verification)
    workload = case / ("recovery-" + case.name)
    proof_path = workload / "verifications" / identifier / "verified.json"
    proof = cleanup.load(proof_path, case)
    config = cleanup.load(workload / "config.json", case)
    if (
        proof["run_id"] != case.name
        or config["run_id"] != case.name
        or proof["verification_id"] != identifier
        or proof["config_sha256"] != cleanup.digest(workload / "config.json")
        or proof["integrity_passed"] is not True
        or type(proof["diagnostic"]) is not bool
        or proof["qualified"] is not (not proof["diagnostic"])
        or proof["passed"] != proof["qualified"]
        or proof["exports"]["duckdb"]["proof"]
        != proof["exports"]["clickhouse"]["proof"]
    ):
        raise RuntimeError(
            "baseline must prove complete integrity with honest diagnostic status"
        )
    names = {p.name for p in workload.glob("*.jsonl")} | {
        "config.json",
        "seed-complete.json",
    }
    if proof["history_sha256"] != {
        n: cleanup.digest(cleanup.regular(workload / n, case)) for n in names
    }:
        raise RuntimeError("external acknowledged history differs from baseline")
    files = {
        p.name: cleanup.digest(cleanup.regular(p, case))
        for p in workload.iterdir()
        if p.is_file()
        and (p.suffix in (".json", ".jsonl") or p.name in ("STOP", "PHASE"))
    }
    for engine in ("duckdb", "clickhouse"):
        export = proof["exports"][engine]
        if export["csv_file"] != engine + "-rows.csv" or export[
            "csv_sha256"
        ] != cleanup.digest(proof_path.parent / export["csv_file"]):
            raise RuntimeError("baseline complete-body export changed")
    return case, workload, proof_path, proof, files


def validate_restore_volume(actual, expected):
    if (
        cleanup.volume_key(actual) != cleanup.volume_key(expected)
        or actual.get("Labels") != expected.get("Labels")
        or actual.get("Driver") != "local"
        or actual.get("Options")
    ):
        raise RuntimeError("restore volume identity or local ownership differs")


def original_plane():
    result = {}
    identifiers = control.call(
        [
            "docker",
            "ps",
            "--no-trunc",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + control.PROJECT,
        ]
    ).split()
    for identifier in identifiers:
        value = control.owned(identifier)
        role = value["Config"]["Labels"]["com.docker.compose.service"]
        if role not in ("storage", "clickhouse") or role in result:
            raise RuntimeError("stop other example workloads before cold readback")
        result[role] = value
    return result


def observed(identifier, project, role):
    value = cold.inspect(identifier)
    labels = value["Config"].get("Labels", {})
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != role
        or Path(labels.get("com.docker.compose.project.working_dir", "/")).resolve()
        != control.ROOT
    ):
        raise RuntimeError("foreign or unproven restore container")
    return value


def service(compose, env, project, role):
    identifiers = control.call(compose + ["ps", "--all", "-q", role], env=env).split()
    if len(identifiers) != 1:
        raise RuntimeError("require exactly one restore " + role)
    return observed(identifiers[0], project, role)


def planned_verifier(name, project):
    identifiers = control.call(
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
    if not identifiers:
        return None  # Successful runtime listing, not a failed inspection.
    if len(identifiers) != 1:
        raise RuntimeError("planned verifier identity is ambiguous")
    value = observed(identifiers[0], project, "verifier")
    if value["Name"] != "/" + name:
        raise RuntimeError("planned verifier name differs")
    return value


def save_logs(root, identifier, name):
    result = subprocess.run(
        ["docker", "logs", identifier],
        env=control.DOCKER_ENV,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=20,
    )
    control.save_text(root / (name + "-console.log"), result.stdout)
    if result.returncode:
        raise RuntimeError("could not retain native restore logs")


def verify_exports(directory, proof, root):
    for engine in ("duckdb", "clickhouse"):
        export = proof["exports"][engine]
        filename = engine + "-rows.csv"
        if (
            export["csv_file"] != filename
            or cleanup.digest(cleanup.regular(directory / filename, root))
            != export["csv_sha256"]
        ):
            raise RuntimeError("restored complete-body artifact differs")


def internal_network(value, project):
    identifiers = {
        n["NetworkID"] for n in value["NetworkSettings"]["Networks"].values()
    }
    if len(identifiers) != 1:
        raise RuntimeError("restore requires exactly one private network")
    (network,) = json.loads(
        control.call(["docker", "network", "inspect", *identifiers])
    )
    if (
        not network["Internal"]
        or network.get("Labels", {}).get("com.docker.compose.project") != project
    ):
        raise RuntimeError("restore network is not isolated and owned")
    return identifiers


def validate_proof(proof, baseline, run_id, verification, binary_sha):
    if (
        proof["run_id"] != run_id
        or proof["verification_id"] != verification
        or proof["config_sha256"] != baseline["config_sha256"]
        or proof["history_sha256"] != baseline["history_sha256"]
        or proof["integrity_passed"] is not True
        or proof["qualified"] != baseline["qualified"]
        or proof["diagnostic"] != baseline["diagnostic"]
        or proof["passed"] != baseline["passed"]
        or proof["snapshot"] != baseline["snapshot"]
        or any(
            proof["exports"][e]["proof"] != baseline["exports"][e]["proof"]
            for e in ("duckdb", "clickhouse")
        )
    ):
        raise RuntimeError("restored bodies, snapshot, witnesses or verdict differ")
    if not re.fullmatch(r"[0-9a-f]{64}", binary_sha):
        raise RuntimeError("missing built workload identity")


def storage_selection(parent, invocation, restored, image=None, transition=None):
    baseline = invocation["services"]["storage"]["image_id"]
    marker = cleanup.regular(parent / "storage-image-transition.json", parent)
    if not marker.exists():
        if image is not None or transition is not None:
            raise RuntimeError("storage upgrade has no registered transition")
        return baseline, None
    intent = cleanup.load(marker, parent)
    if (
        intent["checkpoint_id"] != parent.name
        or baseline != BASELINE_453_IMAGE
        or cleanup.volume_key(intent["restore_volume"]) != cleanup.volume_key(restored)
        or intent["source_image"] != baseline
        or intent["target_image"] != UPGRADE_454_IMAGE
        or intent["target_version"] != "4.54"
        or image != intent["target_image"]
        or transition != cleanup.valid_uuid(intent["transition_id"])
    ):
        raise RuntimeError(
            "restore is reserved for 4.54; refuse implicit downgrade or unknown transition"
        )
    return image, {
        "file": marker.name,
        "sha256": cleanup.digest(marker),
        "transition_id": transition,
    }


def restore_files(storage_image):
    files = ["-f", TEMPLATE]
    if control.lab.PROFILE == "current":
        if storage_image != UPGRADE_454_IMAGE:
            raise RuntimeError("current restore requires the checkpoint's pinned 4.54 image")
        # Reopen with the same bounded reader/cache/capacity configuration.
        files += ["-f", "deploy/compose/docker-compose.storage-current.yml"]
    return files


def run(
    checkpoint_path, case_path, verification, *, storage_image=None, transition=None
):
    control.local_docker()
    parent, invocation, restored = checkpoint(checkpoint_path)
    storage_image, image_intent = storage_selection(
        parent, invocation, restored, storage_image, transition
    )
    compose_files = restore_files(storage_image)
    case, workload, baseline_path, baseline, files = fixture(
        case_path, verification, invocation["source_volume"]
    )
    name = restored["Name"]
    (actual,) = json.loads(control.call(["docker", "volume", "inspect", name]))
    validate_restore_volume(actual, restored)
    if cold.volume_users(name):
        raise RuntimeError("restore volume has another live owner")
    originals = original_plane()
    identifier = uuid.uuid4()
    root = cleanup.regular(
        parent / "native-readback" / str(identifier), cleanup.results_root()
    )
    root.mkdir(parents=True)
    destination = root / ("recovery-" + case.name)
    destination.mkdir()
    for filename, checksum in files.items():
        data = (workload / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != checksum:
            raise RuntimeError("source witness changed during copy")
        control.save_text(destination / filename, data.decode("utf-8"))
    (image,) = json.loads(control.call(["docker", "image", "inspect", cold.IMAGE]))
    if (
        image["Config"].get("Labels", {}).get("org.examples.owner") != control.OWNER
        or image["Config"]["Labels"].get("org.examples.duckdb.version") != "2.0.1"
    ):
        raise RuntimeError("require the actual current-version owned runner")
    project = "iceberg-cold-readback-" + identifier.hex
    env = control.DOCKER_ENV | {
        "COLD_PROJECT": project,
        "COLD_RESULTS": str(root),
        "COLD_RESTORE_VOLUME": name,
        "COLD_STORAGE_IMAGE": storage_image,
        "COLD_QUERY_IMAGE": invocation["services"]["clickhouse"]["image_id"],
        "COLD_RUNNER_IMAGE": image["Id"],
        "COLD_UID": str(os.getuid()),
        "COLD_GID": str(os.getgid()),
    }
    compose = [
        "docker",
        "compose", "--project-directory", str(control.ROOT),
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(control.ROOT),
        *compose_files,
    ]
    frozen = control.sources()
    baseline_sha = cleanup.digest(baseline_path)
    control.save(
        root / "invocation.json",
        {
            "project": project,
            "checkpoint": str(parent.relative_to(control.ROOT)),
            "case": str(case.relative_to(control.ROOT)),
            "baseline_verification": verification,
            "baseline_sha256": baseline_sha,
            "source_witnesses": files,
            "source_sha256": frozen,
            "restore_volume": actual,
            "storage_image": storage_image,
            "storage_image_intent": image_intent,
            "started_ns": time.time_ns(),
        },
    )
    control.save(
        root / "compose.json",
        json.loads(control.call(compose + ["config", "--format", "json"], env=env)),
    )
    if image_intent is not None:
        control.save_text(
            root / "storage-image-transition.json",
            cleanup.regular(parent / image_intent["file"], parent).read_text(),
        )
    sampler = ResourceSampler(root, env=control.DOCKER_ENV)
    sampler.start()
    failure, errors, client, result = None, [], None, None
    body_verified = False
    try:
        control.call(
            compose
            + ["up", "-d", "--wait", "--wait-timeout", "90", "storage", "clickhouse"],
            env=env,
            timeout=120,
        )
        networks = internal_network(service(compose, env, project, "storage"), project)
        for role, disk, cap in (
            ("storage", "/data", "storage"),
            ("clickhouse", "/var/lib/clickhouse", "clickhouse"),
        ):
            value = service(compose, env, project, role)
            control.check_caps(value, cap)
            control.check_mounts(value)
            control.check_networks(value, networks)
            control.save(root / (role + "-started.json"), control.state(value))
            sampler.bind(role, value["Id"], disk)
        if control.lab.PROFILE == "current":
            control.save(
                root / "native-settings.json",
                control.current_settings(
                    service(compose, env, project, "storage"),
                    service(compose, env, project, "clickhouse"),
                ),
            )
        if cold.volume_users(name) != {service(compose, env, project, "storage")["Id"]}:
            raise RuntimeError("restore volume ownership changed")
        control.call(
            [
                "docker",
                "exec",
                service(compose, env, project, "clickhouse")["Id"],
                "clickhouse-client",
                "--password",
                "local-reader",
                "--allow_database_iceberg",
                "1",
                "--query",
                DDL,
            ]
        )
        vid = str(uuid.uuid4())
        gate = "while ! test -f /results/release; do sleep .1; done; exec /usr/local/bin/lab recovery-verify"
        planned_name = project + "-verifier"
        if planned_verifier(planned_name, project) is not None:
            raise RuntimeError("planned verifier already exists")
        control.save(
            root / "planned-verifier.json",
            {
                "name": planned_name,
                "project": project,
                "role": "verifier",
                "image": image["Id"],
            },
        )
        client = planned_name
        returned_id = control.call(
            compose
            + [
                "run",
                "-d",
                "--name",
                client,
                "--no-deps",
                "--entrypoint",
                "/bin/sh",
                "-e",
                "RECOVERY_RUN_ID=" + case.name,
                "-e",
                "RECOVERY_VERIFICATION_ID=" + vid,
                "-e",
                "RECOVERY_DIAGNOSTIC=" + str(baseline["diagnostic"]).lower(),
                "verifier",
                "-c",
                gate,
            ],
            env=env,
        ).strip()
        value = planned_verifier(client, project)
        if value is None or value["Id"] != returned_id:
            raise RuntimeError("created verifier differs from registered identity")
        client = value["Id"]
        control.check_caps(value, "recovery-tools")
        control.check_mounts(value, root)
        control.check_networks(value, networks)
        built = control.built_source_proof(value)
        control.save(root / "built-source.json", built)
        sampler.bind("verifier", client, "/results")
        control.wait_coverage(
            sampler, ["storage", "clickhouse", "verifier"], time.time_ns()
        )
        control.save_text(root / "release", "release\n")
        control.call(["docker", "wait", client], timeout=360)
        value = observed(client, project, "verifier")
        control.save(root / "verifier-ended.json", control.state(value))
        save_logs(root, client, "verifier")
        state = value["State"]
        if (
            state["Running"]
            or state["Pid"] != 0
            or state["OOMKilled"]
            or state["ExitCode"] != (1 if baseline["diagnostic"] else 0)
        ):
            raise RuntimeError(
                "native verifier did not complete with its declared outcome"
            )
        directory = destination / "verifications" / vid
        result = cleanup.load(directory / "verified.json", root)
        validate_proof(result, baseline, case.name, vid, built["binary_sha256"])
        verify_exports(directory, result, root)
        storage = cleanup.load(directory / "storage-metrics.json", root)
        cleanup.check_native_versions(
            directory, result, storage, {"duckdb_version": "2.0.1"}, {}, root
        )
        native = cleanup.load(directory / "native-versions.json", root)
        if (
            native["engine"] != "v2.0.1"
            or native["lab_binary_sha256"] != built["binary_sha256"]
        ):
            raise RuntimeError("actual native version or executable changed")
        if (
            control.sources() != frozen
            or cleanup.digest(baseline_path) != baseline_sha
            or any(cleanup.digest(workload / n) != sha for n, sha in files.items())
        ):
            raise RuntimeError("source code, baseline or original journal changed")
        body_verified = True
    except BaseException as error:
        failure = error
    finally:
        if client:
            try:
                value = planned_verifier(project + "-verifier", project)
                if value is None:
                    control.save(
                        root / "verifier-absent.json",
                        {
                            "name": project + "-verifier",
                            "healthy_runtime_listing": True,
                        },
                    )
                else:
                    if value["State"]["Running"]:
                        control.call(
                            ["docker", "stop", "--timeout", "20", value["Id"]],
                            timeout=30,
                        )
                    value = observed(value["Id"], project, "verifier")
                    control.save(root / "verifier-final.json", control.state(value))
                    if value["State"]["Running"] or value["State"]["Pid"] != 0:
                        raise RuntimeError("native verifier fencing is unknown")
            except BaseException as error:
                errors.append(repr(error))
                failure = failure or error
        for role in ("clickhouse", "storage"):
            try:
                value = service(compose, env, project, role)
                control.call(
                    ["docker", "stop", "--timeout", "30", value["Id"]], timeout=40
                )
                value = observed(value["Id"], project, role)
                control.save(root / (role + "-stopped.json"), control.state(value))
                cold.stopped(value)
            except BaseException as error:
                errors.append(repr(error))
                failure = failure or error
        try:
            _, summary = sampler.close()
            control.save(root / "resource-summary.json", summary)
        except BaseException as error:
            errors.append(repr(error))
            failure = failure or error
        try:
            for role, original in originals.items():
                if control.live_identity(
                    control.owned(original["Id"], role)
                ) != control.live_identity(original):
                    raise RuntimeError("original service changed")
        except BaseException as error:
            errors.append(repr(error))
            failure = failure or error
        control.save(
            root / "result.json",
            {
                "passed": failure is None and result is not None,
                "native_restore_verified": body_verified,
                "error": repr(failure) if failure else None,
                "cleanup_errors": errors,
                "verification": result,
                "completed_ns": time.time_ns(),
                "project_removed": False,
                "scope": "Separate-volume read-only replay of one complete witnessed table; original failed live verdicts are unchanged. Not HA, power loss or capacity qualification.",
            },
        )
    print(root.relative_to(control.ROOT), flush=True)
    if failure:
        raise failure
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--verification", required=True)
    args = parser.parse_args()
    with cold.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run(args.checkpoint, args.case, args.verification)


if __name__ == "__main__":
    main()
