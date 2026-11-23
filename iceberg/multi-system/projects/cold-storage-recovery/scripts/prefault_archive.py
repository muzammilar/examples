"""Archive one sealed pre-fault baseline failure without passing its unrun test.

The two original seals and the completed audit's aggregate hash are explicit
allowlists, not a policy for arbitrary failed runs. Original native lifecycle
facts are fixed by those bytes; every owner is inspected again at publication.
"""

import hashlib
import json
from pathlib import Path
import time
import uuid

import recovery_cleanup as cleanup
import recovery_control as control

RUN_ID = "f1b2af24-f6c2-4bd2-8e44-a35392666f23"
AUDIT_ID = "2e43e19d-c3f6-4964-875c-97c29907beaa"
SOURCE_FILE = "preservation/source-bb372de4951a450a87199e3e0eedeba5/manifest.json"
SOURCE_SHA = "f1b621ec72ddf1d9162070af1f9d27935a74850bf691f264446e572080a1ede5"
ORIGINAL_FILE = "preservation/original-artifacts-bb372de4951a450a87199e3e0eedeba5.json"
ORIGINAL_SHA = "9d23c9a27aa160391e685eb19de8ceea24e3deeb32aa0472cf396e8926335165"
# SHA256 of canonical {case-relative file: SHA256} over 28 audit/export/tool
# files. The independently qualified audit includes stable all-append catalogs,
# complete bodies, Parquet counters and the finalized normal verifier lifecycle.
AUDIT_SHA = "3a0c169543da109cea9109f7af3d877223574d36f67e4fbe8012db5daaf95d5f"
STATUS = "failed-but-archived"


def add_files(case, directories, manifest):
    for directory in directories:
        for path in directory.rglob("*"):
            if path.is_file() and str(path.relative_to(case)) not in manifest:
                cleanup.add_artifact(manifest, path, case)


def seal(case, relative, checksum, manifest, *, source=False):
    path = cleanup.regular(case / relative, case)
    if cleanup.digest(path) != checksum:
        raise RuntimeError("known pre-fault seal changed")
    value = cleanup.load(path, case)
    if value["run_id"] != RUN_ID:
        raise RuntimeError("pre-fault seal identity differs")
    for name, expected in value["files"].items():
        relative_file = Path(name)
        if relative_file.is_absolute() or ".." in relative_file.parts:
            raise RuntimeError("sealed path escaped case")
        target = cleanup.regular((path.parent if source else case) / name, case)
        if (
            cleanup.digest(target) != expected["sha256"]
            or target.stat().st_size != expected["bytes"]
        ):
            raise RuntimeError("sealed original evidence changed: " + name)
        cleanup.add_artifact(manifest, target, case)
    cleanup.add_artifact(manifest, path, case)
    return value


def originals(case, manifest):
    if (
        case.name != RUN_ID
        or any(
            (case / name).exists()
            for name in ("result.json", "compactor-evidence.json")
        )
        or list(case.glob("*kill*.json"))
    ):
        raise RuntimeError("only the sealed, unrun pre-fault case is eligible")
    metadata = cleanup.load(case / "case.json", case)
    source = seal(case, SOURCE_FILE, SOURCE_SHA, manifest, source=True)
    original = seal(case, ORIGINAL_FILE, ORIGINAL_SHA, manifest)
    if (
        source["case_sha256"] != cleanup.digest(case / "case.json")
        or {name: entry["sha256"] for name, entry in source["files"].items()}
        != metadata["source_sha256"]
    ):
        raise RuntimeError("sealed source set differs from original case")
    phases = control.history(case / "phases.jsonl")
    if [row["phase"] for row in phases] != ["baseline", "finished"] or list(
        (case / ("recovery-" + case.name) / "observations").glob("*")
    ):
        raise RuntimeError("pre-fault case unexpectedly reached fault/inspection")
    directories = {
        str(Path(name).parent)
        for name in original["files"]
        if name.endswith("/launch.json")
    }
    # These exact seals bind five clean Go actors/seed, the signature-failed
    # Spark process, and the normally-exited gate aborted by controller cleanup.
    return metadata, directories, phases[-1]["host_ns"]


def clients(case, original_dirs, manifest, *, cutoff=None, native=False):
    found, fences = set(), []
    for parent in ("processes", "maintenance"):
        for directory in sorted((case / parent).iterdir()):
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
                raise RuntimeError("post-failure client is not a read-only verifier")
            if (
                launch["run_id"] != case.name
                or launch["invocation"] != directory.name
                or ended["id"] != started["id"]
                or ended["image_id"] != started["image_id"]
                or ended["restart_count"] != started["restart_count"]
                or ended["state"]["StartedAt"] != started["state"]["StartedAt"]
                or ended["state"]["Running"]
                or ended["state"]["Pid"] != 0
            ):
                raise RuntimeError("pre-fault client identity/fencing differs")
            if (
                relative in original_dirs
                or cutoff is None
                or launch["created_host_ns"] <= cutoff
            ):
                recorded = {
                    key: ended[key]
                    for key in ("id", "image_id", "state", "restart_count")
                }
                if native:
                    value = control.owned(ended["id"], launch["service"])
                    labels = value["Config"]["Labels"]
                    if (
                        control.state(value) != recorded
                        or labels.get("org.examples.recovery.run") != case.name
                        or labels.get("org.examples.recovery.invocation")
                        != directory.name
                    ):
                        raise RuntimeError("native pre-fault client fence differs")
                fences.append(
                    {"directory": relative, "service": launch["service"], **recorded}
                )
                add_files(case, [directory], manifest)
    if found != original_dirs or len({value["id"] for value in fences}) != len(fences):
        raise RuntimeError("original registry/fence identity set differs")
    return fences


def audit_artifacts(case):
    selected = []
    for path in (case / "processes").iterdir():
        launch = cleanup.load(path / "launch.json", case)
        if launch.get("extra", {}).get("RECOVERY_VERIFICATION_ID") == AUDIT_ID:
            selected.append(path)
    if len(selected) != 1:
        raise RuntimeError("one exact data audit client required")
    directories = [
        case / "preservation" / ("data-verification-" + AUDIT_ID),
        case / ("recovery-" + case.name) / "verifications" / AUDIT_ID,
        selected[0],
    ]
    manifest = {}
    add_files(case, directories, manifest)
    checksum = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return checksum, manifest, directories[0]


def evidence(case, *, cutoff=None, native=False):
    case = cleanup.regular(Path(case).resolve(), cleanup.results_root())
    manifest = {}
    metadata, original_dirs, finished = originals(case, manifest)
    fences = clients(case, original_dirs, manifest, cutoff=cutoff, native=native)
    checksum, audited_files, directory = audit_artifacts(case)
    if checksum != AUDIT_SHA:
        raise RuntimeError("known completed audit artifacts changed")
    result, invocation = [
        cleanup.load(directory / name, case)
        for name in ("result.json", "invocation.json")
    ]
    if (
        result["data_verification_passed"] is not True
        or result["original_case_passed"] is not False
        or result["error"] is not None
        or invocation["run_id"] != case.name
        or invocation["verification_id"] != AUDIT_ID
        or invocation["started_ns"] <= finished
    ):
        raise RuntimeError("completed data audit did not qualify")
    reference = result["reference"]
    proof = cleanup.check_verification(
        case, reference, metadata, manifest, invocation["started_ns"]
    )
    if reference["verification_id"] != AUDIT_ID:
        raise RuntimeError("data audit reference differs")
    manifest.update(audited_files)
    return metadata, reference, proof, fences, manifest


def receipt_fields(case, metadata, reference, proof, fences, invocation):
    return {
        "passed": False,
        "status": STATUS,
        "data_preservation_passed": True,
        "fault_injected": False,
        "run_id": case.name,
        "retirement_id": invocation["retirement_id"],
        "invocation": invocation,
        **reference,
        "case_sha256": cleanup.digest(case / "case.json"),
        "source_seal_sha256": SOURCE_SHA,
        "original_seal_sha256": ORIGINAL_SHA,
        "audit_artifacts_sha256": AUDIT_SHA,
        "snapshot": proof["snapshot"],
        "data_verified_ns": proof["utc_ns"],
        "native_fences": fences,
        "volume": metadata["volume"],
    }


def archived_base(case):
    attempts = []
    for directory in (case / "retirements").glob("*"):
        invocation = cleanup.load(directory / "invocation.json", case)
        if invocation["run_id"] != case.name or invocation[
            "retirement_id"
        ] != cleanup.valid_uuid(directory.name):
            raise RuntimeError("pre-fault archive invocation identity differs")
        attempts.append((invocation["started_ns"], directory, invocation))
    if not attempts:
        raise RuntimeError(
            "known failed baseline needs a completed preservation archive"
        )
    _, directory, invocation = max(attempts)
    receipt = cleanup.load(directory / "receipt.json", case)
    if receipt.get("data_preservation_passed") is not True:
        raise RuntimeError("pre-fault archive failed or incomplete")
    metadata, reference, proof, fences, manifest = evidence(
        case, cutoff=receipt["completed_ns"]
    )
    expected = receipt_fields(case, metadata, reference, proof, fences, invocation)
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise RuntimeError("pre-fault archive identity/seal/fence differs")
    cleanup.add_artifact(manifest, directory / "invocation.json", case)
    if manifest != receipt["artifacts"]:
        raise RuntimeError("pre-fault archive artifact manifest changed")
    cleanup.add_artifact(manifest, directory / "receipt.json", case)
    # Later read-only history attempts belong to their new receipts, not the old
    # archive's frozen manifest. Their fencing is still mandatory.
    sealed = cleanup.load(case / ORIGINAL_FILE, case)
    original_dirs = {
        str(Path(name).parent)
        for name in sealed["files"]
        if name.endswith("/launch.json")
    }
    clients(case, original_dirs, manifest)
    return receipt, directory / "receipt.json", manifest


def archive_case(case, audit_directory):
    """Caller holds controller.lock and pins local_docker; no SUT mutation occurs."""
    case = Path(case).resolve()
    if Path(audit_directory).resolve() != case / "preservation" / (
        "data-verification-" + AUDIT_ID
    ):
        raise RuntimeError("only the known completed data audit may be adopted")
    metadata, reference, proof, _, _ = evidence(case)
    directory = case / "retirements" / str(uuid.uuid4())
    directory.mkdir(parents=True)
    invocation = {
        "run_id": case.name,
        "retirement_id": directory.name,
        "started_ns": time.time_ns(),
    }
    control.save(directory / "invocation.json", invocation)
    try:
        inventory, groups = cleanup.project_inventory(), cleanup.cases_by_volume()
        key = cleanup.volume_key(metadata["volume"])
        if cleanup.current_volume(inventory) != key:
            raise RuntimeError("pre-fault archive current volume differs")
        tables = cleanup.catalog_recoveries(control.service("storage"))
        if (
            set(tables) != {item.name for item, _ in groups[key]}
            or tables[case.name] != proof["snapshot"]["table_uuid"]
        ):
            raise RuntimeError("pre-fault archive native UUID set differs")
        stamp = cleanup.fixture_stamp([(case, metadata)])
        if proof["utc_ns"] < stamp["latest_operation_ns"]:
            raise RuntimeError("data audit predates an operation on this case")
        metadata, reference, proof, fences, manifest = evidence(case, native=True)
        cleanup.add_artifact(manifest, directory / "invocation.json", case)
        receipt = {
            **receipt_fields(case, metadata, reference, proof, fences, invocation),
            "verification_scope": "Historical audit only; later volume faults require normal verify-history before teardown",
            "fixture": stamp,
            "artifacts": manifest,
            "completed_ns": time.time_ns(),
        }
    except BaseException as primary:
        try:
            control.save(
                directory / "receipt.json",
                {
                    "passed": False,
                    "status": "failed-prefault-archive",
                    "data_preservation_passed": False,
                    "run_id": case.name,
                    "retirement_id": directory.name,
                    "invocation": invocation,
                    "error": repr(primary),
                    "completed_ns": time.time_ns(),
                },
            )
        except BaseException as secondary:
            primary.add_note("Could not save failed archive: " + repr(secondary))
        raise
    control.save(directory / "receipt.json", receipt)
    archived_base(case)
    return directory / "receipt.json"
