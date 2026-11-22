"""Preserve explicitly reviewed failed runs without changing their verdicts.

Plans pin original seals and an independently restored full-body proof. Receipts
do not synthesize missing operational evidence or make a failed run pass.
"""

import argparse
import fcntl
from pathlib import Path
import time
import uuid

import cold_cleanup
import cold_readback
import cold_storage
import recovery_cleanup as cleanup
import recovery_control as control

PLANS = control.ROOT / "failed-preservation-plans.json"
DIRECTORY = "preservation-receipts"


def plan_for(case):
    plans = cleanup.load(PLANS, control.ROOT)
    return plans.get(case.name)


def local_path(root, name):
    path = Path(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise RuntimeError("preservation reference escaped its owner")
    return cleanup.regular(root / path, root)


def pinned(root, reference):
    path = local_path(root, reference["file"])
    if cleanup.digest(path) != reference["sha256"]:
        raise RuntimeError("reviewed preservation input changed: " + reference["file"])
    return cleanup.load(path, root)


def controller_source(name):
    """Materialize only owned source or a repository-tracked shared source link."""
    relative = Path(name)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise RuntimeError("controller source escaped the example")
    path = control.ROOT / relative
    target = path.resolve()
    if target == path:
        return cleanup.regular(path, control.ROOT)
    repository = control.ROOT.parents[1]
    cleanup.regular(target, repository)
    control.call(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "--error-unmatch",
            "--",
            str(target.relative_to(repository)),
        ]
    )
    return target


def original_evidence(value):
    case = cleanup.regular(Path(value).resolve(), cleanup.results_root())
    plan = plan_for(case)
    if not plan or local_path(control.ROOT, plan["case"]) != case:
        raise RuntimeError("failed run has no matching reviewed preservation plan")
    metadata = cleanup.load(case / "case.json", case)
    failure = cleanup.load(case / "failure.json", case)
    if (
        cleanup.digest(case / "case.json") != plan["case_sha256"]
        or metadata["run_id"] != case.name
        or metadata["project"] != control.PROJECT
        or metadata["kind"] not in cleanup.CASE_KINDS
        or failure.get("passed") is not False
        or failure.get("native_storage_preserved") is not True
        or (case / "result.json").exists()
    ):
        raise RuntimeError(
            "original failed identity, verdict or storage status changed"
        )
    source = pinned(case, plan["source_seal"])
    original = pinned(case, plan["original_seal"])
    if (
        source["run_id"] != case.name
        or original["run_id"] != case.name
        or source["case_sha256"] != plan["case_sha256"]
        or source["files"] != metadata["source_sha256"]
        or not original["files"]
    ):
        raise RuntimeError("original source/artifact seal differs from failed case")
    source_root = local_path(case, plan["source_seal"]["source_root"])
    for name, sha in source["files"].items():
        if cleanup.digest(local_path(source_root, name)) != sha:
            raise RuntimeError("preserved original source changed")
    for name, expected in original["files"].items():
        path = local_path(case, name)
        if (
            cleanup.digest(path) != expected["sha256"]
            or path.stat().st_size != expected["bytes"]
        ):
            raise RuntimeError("preserved original artifact changed")
    original_dirs = {
        str(Path(n).parent) for n in original["files"] if n.endswith("/launch.json")
    }
    clients, found = [], set()
    for parent in ("processes", "maintenance"):
        for directory in sorted((case / parent).iterdir()):
            cleanup.regular(directory, case)
            relative = str(directory.relative_to(case))
            launch, started, ended = [
                cleanup.load(directory / name, case)
                for name in ("launch.json", "started.json", "ended.json")
            ]
            if relative in original_dirs:
                found.add(relative)
            elif (
                parent != "processes"
                or launch["service"] != "recovery-tools"
                or launch["command"] != "recovery-verify"
            ):
                raise RuntimeError(
                    "later failed-case actor is not a read-only verifier"
                )
            if (
                launch["run_id"] != case.name
                or launch["invocation"] != directory.name
                or any(
                    ended[k] != started[k] for k in ("id", "image_id", "restart_count")
                )
                or ended["state"]["StartedAt"] != started["state"]["StartedAt"]
                or ended["state"]["Running"]
                or ended["state"]["Pid"] != 0
            ):
                raise RuntimeError(
                    "failed-case actor identity or terminal fence differs"
                )
            clients.append(
                {
                    "directory": relative,
                    "launch": launch,
                    "record": {
                        k: ended[k]
                        for k in ("id", "image_id", "state", "restart_count")
                    },
                }
            )
    if (
        not original_dirs
        or found != original_dirs
        or len({v["record"]["id"] for v in clients}) != len(clients)
    ):
        raise RuntimeError("original actor registry is incomplete or duplicated")
    files, latest = {}, metadata["created_ns"]
    for path in sorted(case.rglob("*")):
        relative = path.relative_to(case)
        if relative.parts[0] == DIRECTORY:
            continue
        cleanup.regular(path, case)
        if path.is_file():
            files[str(relative)] = cleanup.digest(path)
            if path.suffix in (".json", ".jsonl"):
                latest = max(latest, cleanup.event_time(path))
    return {
        "metadata": metadata,
        "plan": plan,
        "files": files,
        "clients": clients,
        "latest_operation_ns": latest,
    }


def fingerprint(case):
    evidence = original_evidence(case)
    return {
        "files": {
            str((case / n).relative_to(control.ROOT)): sha
            for n, sha in evidence["files"].items()
        },
        "latest_operation_ns": evidence["latest_operation_ns"],
    }


def preservation(case, evidence):
    plan = evidence["plan"]
    parent = (
        cleanup.results_root()
        / "cold-storage"
        / cleanup.valid_uuid(plan["checkpoint"]["id"])
    )
    parent, checkpoint, restored = cold_readback.checkpoint(parent)
    if cleanup.digest(parent / "storage.tar.gz") != plan["checkpoint"][
        "archive_sha256"
    ] or cleanup.volume_key(checkpoint["source_volume"]) != cleanup.volume_key(
        evidence["metadata"]["volume"]
    ):
        raise RuntimeError("reviewed checkpoint is not the original failed fixture")
    _, _, baseline_path, baseline, witnesses = cold_readback.fixture(
        case, plan["baseline"]["verification_id"], checkpoint["source_volume"]
    )
    if (
        cleanup.digest(baseline_path) != plan["baseline"]["sha256"]
        or baseline["qualified"] is not False
        or baseline["diagnostic"] is not True
    ):
        raise RuntimeError("baseline must retain its reviewed failed qualification")
    root = parent / "native-readback" / cleanup.valid_uuid(plan["readback"]["id"])
    selected = cold_cleanup.plan(root, "readback")
    invocation = cleanup.load(root / "invocation.json", root)
    proof = cleanup.load(root / "result.json", root)["verification"]
    directory = (
        root
        / ("recovery-" + case.name)
        / "verifications"
        / cleanup.valid_uuid(proof["verification_id"])
    )
    built = cleanup.load(root / "built-source.json", root)
    if (
        cleanup.digest(directory / "verified.json") != plan["readback"]["proof_sha256"]
        or invocation["case"] != str(case.relative_to(control.ROOT))
        or invocation["baseline_verification"] != plan["baseline"]["verification_id"]
        or invocation["baseline_sha256"] != plan["baseline"]["sha256"]
        or invocation["source_witnesses"] != witnesses
    ):
        raise RuntimeError("restored proof does not replay the reviewed source witness")
    cold_readback.validate_proof(
        proof, baseline, case.name, proof["verification_id"], built["binary_sha256"]
    )
    native = {}
    metrics = cleanup.load(directory / "storage-metrics.json", root)
    cleanup.check_native_versions(
        directory, proof, metrics, {"duckdb_version": "2.0.1"}, native, root
    )
    external = {
        str((root / n).relative_to(control.ROOT)): sha
        for n, sha in selected["artifacts"].items()
    }
    for name, sha in witnesses.items():
        path = root / ("recovery-" + case.name) / name
        if cleanup.digest(cleanup.regular(path, root)) != sha:
            raise RuntimeError("restored witness bytes changed")
    for scope in (parent, root):
        for path in scope.iterdir():
            cleanup.regular(path, scope)
            if path.is_file():
                external[str(path.relative_to(control.ROOT))] = cleanup.digest(path)
    external.update(
        {str((root / n).relative_to(control.ROOT)): sha for n, sha in native.items()}
    )
    external[str((directory / "storage-metrics.json").relative_to(control.ROOT))] = (
        cleanup.digest(directory / "storage-metrics.json")
    )
    return {
        "snapshot": proof["snapshot"],
        "body_proof": proof["exports"]["duckdb"]["proof"],
        "verification_id": proof["verification_id"],
        "verification_sha256": plan["readback"]["proof_sha256"],
        "checkpoint_id": parent.name,
        "restore_volume": restored,
        "external_artifacts": external,
    }


def native_fences(case, clients):
    # A failed inspect is never evidence that a process disappeared.
    identifiers = set(
        control.call(["docker", "ps", "--all", "--no-trunc", "-q"]).split()
    )
    if control.call(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=org.examples.recovery.run=" + case.name,
        ]
    ).strip():
        raise RuntimeError("failed-case actor is still running")
    fences = []
    for client in clients:
        record, launch = client["record"], client["launch"]
        value = None
        if record["id"] in identifiers:
            raw = control.owned(record["id"], launch["service"])
            labels = raw["Config"]["Labels"]
            value = control.state(raw)
            if (
                value != record
                or labels.get("org.examples.recovery.run") != case.name
                or labels.get("org.examples.recovery.invocation")
                != launch["invocation"]
            ):
                raise RuntimeError("native actor identity or terminal fence differs")
        fences.append(
            {"directory": client["directory"], "record": record, "native": value}
        )
    return {
        "all_container_ids": sorted(identifiers),
        "fences": fences,
        "observed_ns": time.time_ns(),
    }


def archive(case):
    """Read-only publication; no teardown hook consumes this receipt yet."""
    case = Path(case).resolve()
    evidence = original_evidence(case)
    proof = preservation(case, evidence)
    directory = cleanup.regular(case / DIRECTORY / str(uuid.uuid4()), case)
    directory.mkdir(parents=True)
    invocation = {
        "run_id": case.name,
        "archive_id": directory.name,
        "started_ns": time.time_ns(),
        "plan_sha256": cleanup.digest(PLANS),
        "source_sha256": control.sources(),
    }
    control.save(directory / "invocation.json", invocation)
    try:
        for name, sha in invocation["source_sha256"].items():
            source = controller_source(name)
            data = source.read_bytes()
            if cleanup.digest(source) != sha:
                raise RuntimeError("controller source changed before preservation")
            destination = local_path(directory, "controller-source/" + name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            control.save_text(destination, data.decode())
        heads = cleanup.catalog_recoveries(control.service("storage"), snapshots=True)
        if heads.get(case.name) != proof["snapshot"]:
            raise RuntimeError("current native head differs from the restored snapshot")
        fences = native_fences(case, evidence["clients"])
        # Recheck the source and entire original witness after the external proof.
        if (
            original_evidence(case) != evidence
            or control.sources() != invocation["source_sha256"]
        ):
            raise RuntimeError("evidence or controller changed during preservation")
        receipt = {
            "passed": False,
            "original_status": "failed",
            "data_preservation_passed": True,
            "run_id": case.name,
            "invocation": invocation,
            "artifacts": evidence["files"],
            "preservation": proof,
            "native_heads": heads,
            "native_fences": fences,
            "completed_ns": time.time_ns(),
            "teardown_authorized": False,
        }
    except BaseException as error:
        control.save(
            directory / "receipt.json",
            {
                "passed": False,
                "data_preservation_passed": False,
                "invocation": invocation,
                "error": repr(error),
                "completed_ns": time.time_ns(),
            },
        )
        raise
    control.save(directory / "receipt.json", receipt)
    return directory / "receipt.json"


def replay(case):
    """Recheck the latest receipt offline; any newer incomplete attempt blocks it."""
    case = cleanup.regular(Path(case).resolve(), cleanup.results_root())
    attempts = []
    for directory in (case / DIRECTORY).glob("*"):
        cleanup.regular(directory, case)
        cleanup.valid_uuid(directory.name)
        invocation = cleanup.load(directory / "invocation.json", case)
        if (
            invocation["run_id"] != case.name
            or invocation["archive_id"] != directory.name
        ):
            raise RuntimeError("preservation invocation identity differs")
        attempts.append((invocation["started_ns"], directory, invocation))
    if not attempts:
        raise RuntimeError("failed case has no completed preservation receipt")
    _, directory, invocation = max(attempts)
    receipt = cleanup.load(directory / "receipt.json", case)
    if (
        receipt.get("passed") is not False
        or receipt.get("original_status") != "failed"
        or receipt.get("data_preservation_passed") is not True
        or receipt.get("teardown_authorized") is not False
        or receipt.get("run_id") != case.name
        or receipt.get("invocation") != invocation
        or invocation["plan_sha256"] != cleanup.digest(PLANS)
    ):
        raise RuntimeError("preservation receipt is incomplete or changed its verdict")
    evidence = original_evidence(case)
    proof = preservation(case, evidence)
    if (
        receipt["artifacts"] != evidence["files"]
        or receipt["preservation"] != proof
        or receipt["native_heads"].get(case.name) != proof["snapshot"]
    ):
        raise RuntimeError(
            "preserved source, witness, restore proof or native snapshot changed"
        )
    for name, sha in invocation["source_sha256"].items():
        if cleanup.digest(local_path(directory, "controller-source/" + name)) != sha:
            raise RuntimeError("preserved controller source changed")
    saved = receipt["native_fences"]
    identifiers = saved["all_container_ids"]
    expected = {v["directory"]: v["record"] for v in evidence["clients"]}
    if (
        len(set(identifiers)) != len(identifiers)
        or len(saved["fences"]) != len(expected)
        or {v["directory"] for v in saved["fences"]} != set(expected)
    ):
        raise RuntimeError("saved runtime listing or fence set differs")
    for fence in saved["fences"]:
        record = expected[fence["directory"]]
        native = record if record["id"] in identifiers else None
        if fence["record"] != record or fence["native"] != native:
            raise RuntimeError("saved native proof does not fence the original actor")
    if not invocation["started_ns"] <= saved["observed_ns"] <= receipt["completed_ns"]:
        raise RuntimeError("native fences do not belong to this preservation attempt")
    return directory / "receipt.json"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case")
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    with cold_storage.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.replay:
            path = replay(args.case)
        else:
            control.local_docker()
            path = archive(args.case)
        print(path.relative_to(control.ROOT))
