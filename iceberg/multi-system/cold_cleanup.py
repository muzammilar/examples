"""Remove fenced cold-test tools, never checkpoint/source database volumes."""

import argparse
import fcntl
import json
from pathlib import Path
import re
import time
import uuid

import cold_readback as readback
import cold_storage as cold
import recovery_cleanup as cleanup
import recovery_control as control

PROJECT_LABEL = "com.docker.compose.project"
LEGACY_PERMISSION_CASE = "710e92e7-1532-4081-b1fa-d1024e8d0a90"
LEGACY_SOURCE = "7c88893da5b48558e32ef23f0d9997bbf7cbf1f357cb91228fc1ee41dd1f83b7"


def fenced(record):
    state = record["state"]
    if (
        state["Running"] is not False
        or state["Pid"] != 0
        or state.get("Paused")
        or state.get("Restarting")
        or state.get("Dead")
        or not state.get("StartedAt", "").startswith("20")
        or not state.get("FinishedAt", "").startswith("20")
        or not re.fullmatch(r"[0-9a-f]{64}", record["id"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", record["image_id"])
    ):
        raise RuntimeError("positive stopped-process identity is required")


def identity(record):
    return (
        record["id"],
        record["image_id"],
        record["restart_count"],
        record["state"]["StartedAt"],
    )


def owned(value):
    path = Path(value)
    if not path.is_absolute():
        path = control.ROOT / path
    return cleanup.regular(path, cleanup.results_root()).resolve()


def storage_image(path, parent, checkpoint, restored, invocation):
    baseline = checkpoint["services"]["storage"]["image_id"]
    image = invocation.get("storage_image", baseline)
    reference = invocation.get("storage_image_intent")
    if image == baseline:
        if reference is not None:
            raise RuntimeError("baseline storage has an unexpected upgrade reference")
        return image
    if not isinstance(reference, dict):
        raise RuntimeError("upgraded storage lacks a registered transition")
    selected, expected = readback.storage_selection(
        parent, checkpoint, restored, image, reference.get("transition_id")
    )
    if (
        reference != expected
        or cleanup.digest(cleanup.regular(path / "storage-image-transition.json", path))
        != expected["sha256"]
    ):
        raise RuntimeError("saved storage-image transition differs")
    return selected


def pinned_digest(reference):
    if not isinstance(reference, str) or not re.fullmatch(
        r"(?:[^@\s]+@)?sha256:[0-9a-f]{64}", reference
    ):
        raise RuntimeError("rendered restore image is not digest-pinned")
    return reference.rsplit("@", 1)[-1]


def plan(path, mode):
    path = owned(path)
    run_id = cleanup.valid_uuid(path.name)
    invocation = cleanup.load(path / "invocation.json", path)
    result = cleanup.load(path / "result.json", path)
    artifacts = {}

    def evidence(name):
        file = cleanup.regular(path / name, path)
        artifacts[name] = cleanup.digest(file)
        return json.loads(file.read_text()) if file.suffix == ".json" else None

    evidence("invocation.json")
    evidence("result.json")
    specs = []
    query = network = None
    if mode == "readback":
        if path.parent.name != "native-readback":
            raise RuntimeError("expected a checkpoint native-readback directory")
        parent, checkpoint, restored = readback.checkpoint(path.parent.parent)
        project = "iceberg-cold-readback-" + uuid.UUID(run_id).hex
        if (
            invocation["project"] != project
            or owned(invocation["checkpoint"]) != parent
            or cleanup.volume_key(invocation["restore_volume"])
            != cleanup.volume_key(restored)
            or result["passed"] is not True
            or result["native_restore_verified"] is not True
            or result.get("error")
            or result.get("cleanup_errors")
        ):
            raise RuntimeError("passed, owned native restore proof is required")
        proof = result["verification"]
        if proof["integrity_passed"] is not True:
            raise RuntimeError("restore integrity did not pass")
        case_id = cleanup.valid_uuid(proof["run_id"])
        verification_id = cleanup.valid_uuid(proof["verification_id"])
        directory = path / ("recovery-" + case_id) / "verifications" / verification_id
        if cleanup.load(directory / "verified.json", path) != proof:
            raise RuntimeError("saved native verification differs from result")
        readback.verify_exports(directory, proof, path)
        for file in (directory / "verified.json", *directory.glob("*-rows.csv")):
            artifacts[str(file.relative_to(path))] = cleanup.digest(file)
        for name in (
            "resource-summary.json",
            "resources.jsonl",
            "built-source.json",
            "verifier-console.log",
        ):
            evidence(name)
        compose = evidence("compose.json")
        planned = evidence("planned-verifier.json")
        if (
            compose["name"] != project
            or planned
            != {
                "name": project + "-verifier",
                "project": project,
                "role": "verifier",
                "image": planned.get("image"),
            }
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", planned.get("image", ""))
        ):
            raise RuntimeError("verifier ownership/image is not recorded exactly")
        images = {
            r: checkpoint["services"][r]["image_id"] for r in ("storage", "clickhouse")
        }
        images["storage"] = storage_image(
            path, parent, checkpoint, restored, invocation
        )
        if invocation.get("storage_image_intent") is not None:
            evidence("storage-image-transition.json")
        # Accept the immutable name@digest form too. Runtime preflight still
        # requires every exact container Image ID to match the recorded ID.
        if any(pinned_digest(compose["services"][r]["image"]) != images[r] for r in images):
            raise RuntimeError("rendered service images differ from checkpoint")
        images["verifier"] = planned["image"]
        query, network = project + "_query", project + "_default"
        mounts = {
            "storage": [("volume", restored["Name"], "/data", True)],
            "clickhouse": [
                ("volume", query, "/var/lib/clickhouse", True),
                (
                    "bind",
                    str(control.ROOT / "config/clickhouse.xml"),
                    "/etc/clickhouse-server/config.d/lab.xml",
                    False,
                ),
            ],
            "verifier": [("bind", str(path), "/results", True)],
        }
        for role in ("verifier", "clickhouse", "storage"):
            final = evidence(
                role + ("-final.json" if role == "verifier" else "-stopped.json")
            )
            earlier = evidence(
                role + ("-ended.json" if role == "verifier" else "-started.json")
            )
            fenced(final)
            expected_exit = (
                (1 if proof["diagnostic"] else 0) if role == "verifier" else 0
            )
            if (
                identity(final) != identity(earlier)
                or final["image_id"] != images[role]
                or final["state"]["ExitCode"] != expected_exit
                or final["state"]["OOMKilled"]
                or final["state"].get("Error")
            ):
                raise RuntimeError("stopped readback identity/verdict differs")
            specs.append(
                {
                    "role": role,
                    "final": final,
                    "name": project
                    + ("-verifier" if role == "verifier" else "-" + role + "-1"),
                    "mounts": mounts[role],
                }
            )
        label, value = PROJECT_LABEL, project
        retained = [checkpoint["source_volume"]["Name"], restored["Name"]]
        retained_expected = {
            retained[0]: checkpoint["source_volume"],
            retained[1]: restored,
        }
    else:
        if (
            path.parent != cleanup.results_root() / "cold-storage"
            or invocation["run_id"] != run_id
            or result["run_id"] != run_id
            or invocation["restore_volume"] != "iceberg-cold-" + uuid.UUID(run_id).hex
        ):
            raise RuntimeError("checkpoint identity differs")
        for role in ("backup", "restore"):
            if not (path / (role + "-created.json")).exists():
                if (path / (role + "-final.json")).exists():
                    raise RuntimeError("final utility has no creation record")
                continue
            created, final = (
                evidence(role + "-created.json"),
                evidence(role + "-final.json"),
            )
            evidence(role + "-console.log")
            evidence(role + "-resources.json")
            fenced(final)
            if (
                created["id"] != final["id"]
                or created["image_id"] != final["image_id"]
                or created["restart_count"] != final["restart_count"]
                or final["image_id"] != invocation["utility_image"]
            ):
                raise RuntimeError("utility creation/final image identity differs")
            source = (
                invocation["source_volume"]["Name"]
                if role == "backup"
                else invocation["restore_volume"]
            )
            legacy = (
                run_id == LEGACY_PERMISSION_CASE
                and role == "backup"
                and result["passed"] is False
                and final["state"]["ExitCode"] == 123
                and invocation["source_sha256"]["cold_storage.py"] == LEGACY_SOURCE
            )
            specs.append(
                {
                    "role": role,
                    "final": final,
                    "name": "iceberg-cold-" + uuid.UUID(run_id).hex + "-" + role,
                    "mounts": [
                        ("bind", str(path), "/backup", True),
                        (
                            "volume",
                            source,
                            "/source" if role == "backup" else "/restore",
                            role == "restore",
                        ),
                    ],
                    "legacy": legacy,
                }
            )
        if not specs:
            raise RuntimeError("no recorded utilities to clean")
        label, value = cold.LABEL, run_id
        retained = [invocation["source_volume"]["Name"], invocation["restore_volume"]]
        retained_expected = {retained[0]: invocation["source_volume"]}
        if (path / "restore-volume.json").exists():
            restored = evidence("restore-volume.json")
            if restored["Name"] != retained[1]:
                raise RuntimeError("checkpoint restore volume name differs")
            retained_expected[retained[1]] = restored
    if len({s["final"]["id"] for s in specs}) != len(specs):
        raise RuntimeError("duplicate recorded container identity")
    if query in retained:
        raise RuntimeError("temporary volume overlaps retained database storage")
    return {
        "path": path,
        "mode": mode,
        "label": label,
        "value": value,
        "specs": specs,
        "query": query,
        "network": network,
        "retained": retained,
        "retained_expected": retained_expected,
        "artifacts": artifacts,
    }


def validate_container(raw, spec, selected):
    final = spec["final"]
    current = control.state(raw)
    fenced(current)
    labels, host = raw["Config"].get("Labels") or {}, raw["HostConfig"]
    if (
        identity(current) != identity(final)
        or current["state"]["ExitCode"] != final["state"]["ExitCode"]
        or current["state"]["OOMKilled"] != final["state"]["OOMKilled"]
        or current["state"].get("Error") != final["state"].get("Error")
        or current["state"]["FinishedAt"] != final["state"]["FinishedAt"]
        or raw["Name"] != "/" + spec["name"]
        or labels.get(selected["label"]) != selected["value"]
    ):
        raise RuntimeError("current container differs from fenced recorded identity")
    actual = []
    for mount in raw["Mounts"]:
        source = (
            mount.get("Name")
            if mount["Type"] == "volume"
            else str(control.host_bind_source(mount["Source"]))
        )
        actual.append((mount["Type"], source, mount["Destination"], mount["RW"]))
    if sorted(actual) != sorted(spec["mounts"]):
        raise RuntimeError("container mounts differ from the exact owned mounts")
    if selected["mode"] == "readback":
        working_dir = labels.get("com.docker.compose.project.working_dir", "")
        if (
            labels.get("com.docker.compose.service") != spec["role"]
            or not working_dir
            or not Path(working_dir).is_absolute()
            or Path(working_dir).resolve() != control.ROOT
            or host["NetworkMode"] != selected["network"]
            or set(raw["NetworkSettings"]["Networks"]) != {selected["network"]}
        ):
            raise RuntimeError("Compose working directory/service/network differs")
        control.check_caps(
            raw, "recovery-tools" if spec["role"] == "verifier" else spec["role"]
        )
    else:
        caps = [] if spec["legacy"] else cold.capabilities(spec["role"])
        cap_drop = {c.removeprefix("CAP_") for c in host.get("CapDrop") or []}
        cap_add = {c.removeprefix("CAP_") for c in host.get("CapAdd") or []}
        if (
            host["NetworkMode"] != "none"
            or host["NanoCpus"] != 1000000000
            or host["Memory"] != 512 * 1024**2
            or host["MemorySwap"] != 512 * 1024**2
            or not host["ReadonlyRootfs"]
            or raw["Config"]["User"] != "0:0"
            or cap_drop != {"ALL"}
            or cap_add != set(caps)
            or "no-new-privileges:true" not in (host.get("SecurityOpt") or [])
        ):
            raise RuntimeError("utility isolation/capabilities differ")


def listing(args):
    return set(control.call(["docker", *args]).split())


def inspect_resource(kind, name):
    (value,) = json.loads(control.call(["docker", kind, "inspect", name]))
    return value


def preflight(selected):
    identifiers = listing(["ps", "--all", "--no-trunc", "-q"])
    scoped = listing(
        [
            "ps",
            "--all",
            "--no-trunc",
            "-q",
            "--filter",
            "label=" + selected["label"] + "=" + selected["value"],
        ]
    )
    expected = {s["final"]["id"] for s in selected["specs"]}
    if scoped - expected or scoped - identifiers:
        raise RuntimeError("unregistered containers share cleanup ownership")
    containers = {}
    native_listings = {
        "all_container_ids": sorted(identifiers),
        "owned_container_ids": sorted(scoped),
    }
    for spec in selected["specs"]:
        identifier = spec["final"]["id"]
        if identifier in identifiers:
            raw = cold.inspect(identifier)  # Inspection errors never mean absence.
            validate_container(raw, spec, selected)
            containers[identifier] = raw
    volumes = listing(["volume", "ls", "-q"])
    native_listings["volume_names"] = sorted(volumes)
    retained = {
        name: inspect_resource("volume", name)
        for name in selected["retained"]
        if name in volumes
    }
    for name, observed in retained.items():
        if observed["Name"] != name:
            raise RuntimeError("native retained volume name differs")
        if name in selected["retained_expected"]:
            readback.validate_restore_volume(
                observed, selected["retained_expected"][name]
            )
    if selected["mode"] == "readback" and selected["retained"][1] not in retained:
        raise RuntimeError("readback restore volume is absent; retain its storage")
    network = query = None
    if selected["mode"] == "readback":
        project_filter = [
            "--filter",
            "label=" + PROJECT_LABEL + "=" + selected["value"],
        ]
        scoped_volumes = listing(["volume", "ls", "-q", *project_filter])
        scoped_networks = listing(
            ["network", "ls", "--no-trunc", "-q", *project_filter]
        )
        native_listings.update(
            owned_volume_names=sorted(scoped_volumes),
            owned_network_ids=sorted(scoped_networks),
        )
        if (
            scoped_volumes - {selected["query"]}
            or scoped_volumes - volumes
            or len(scoped_networks) > 1
        ):
            raise RuntimeError("unregistered project volumes/networks")
        if selected["query"] in volumes:
            query = inspect_resource("volume", selected["query"])
            labels = query.get("Labels") or {}
            if (
                query["Name"] not in scoped_volumes
                or labels.get(PROJECT_LABEL) != selected["value"]
                or labels.get("com.docker.compose.volume") != "query"
                or query["Driver"] != "local"
                or query.get("Options")
            ):
                raise RuntimeError("query volume ownership differs")
            users = listing(
                [
                    "ps",
                    "--all",
                    "--no-trunc",
                    "-q",
                    "--filter",
                    "volume=" + selected["query"],
                ]
            )
            if users - expected:
                raise RuntimeError("unregistered query-volume user")
        all_networks = dict(
            line.split(maxsplit=1)[::-1]
            for line in control.call(
                [
                    "docker",
                    "network",
                    "ls",
                    "--no-trunc",
                    "--format",
                    "{{.ID}} {{.Name}}",
                ]
            ).splitlines()
        )
        native_listings["networks_by_name"] = all_networks
        if selected["network"] in all_networks:
            network = inspect_resource("network", all_networks[selected["network"]])
            labels = network.get("Labels") or {}
            if (
                network["Id"] not in scoped_networks
                or network["Name"] != selected["network"]
                or labels.get(PROJECT_LABEL) != selected["value"]
                or labels.get("com.docker.compose.network") != "default"
                or network["Driver"] != "bridge"
                or network["Internal"] is not True
                or set(network.get("Containers") or {}) - expected
            ):
                raise RuntimeError("private network ownership/endpoints differ")
        elif scoped_networks:
            raise RuntimeError("unregistered project network")
    return {
        "containers": containers,
        "network": network,
        "query": query,
        "retained": retained,
        "native_listings": native_listings,
    }


def run(path, mode):
    control.local_docker()
    selected = plan(path, mode)
    receipt = cleanup.regular(
        selected["path"] / "cleanup" / str(uuid.uuid4()), selected["path"]
    )
    receipt.mkdir(parents=True)
    removed, failure, observed_retained = [], None, None
    try:
        before = preflight(selected)
        observed_retained = before["retained"]
        control.save(receipt / "before.json", before)
        control.save(receipt / "evidence.json", selected["artifacts"])
        for name, digest in selected["artifacts"].items():
            if cleanup.digest(selected["path"] / name) != digest:
                raise RuntimeError(
                    "evidence changed before cleanup; preserve resources"
                )
        for spec in selected["specs"]:
            identifier = spec["final"]["id"]
            if identifier in before["containers"]:
                validate_container(cold.inspect(identifier), spec, selected)
                control.call(["docker", "rm", identifier])  # No force or volume flag.
                removed.append({"container": identifier})
        if before["network"]:
            control.call(["docker", "network", "rm", before["network"]["Id"]])
            removed.append({"network": before["network"]["Id"]})
        if before["query"]:
            control.call(["docker", "volume", "rm", selected["query"]])
            removed.append({"query_volume": selected["query"]})
        after = preflight(selected)
        control.save(receipt / "after.json", after)
        if (
            after["containers"]
            or after["network"]
            or after["query"]
            or after["retained"] != before["retained"]
        ):
            raise RuntimeError("native absence/retained-volume identity check failed")
        for name, digest in selected["artifacts"].items():
            if cleanup.digest(selected["path"] / name) != digest:
                raise RuntimeError("preserved evidence changed during cleanup")
    except BaseException as error:
        failure = error
    control.save(
        receipt / "result.json",
        {
            "passed": failure is None,
            "error": str(failure) if failure else None,
            "mode": mode,
            "removed": removed,
            "observed_retained_volumes": observed_retained,
            "already_absent_retained_volumes": sorted(
                set(selected["retained"]) - set(observed_retained)
            )
            if observed_retained is not None
            else None,
            "completed_ns": time.time_ns(),
            "scope": "Temporary fenced resources only; checkpoint/source volumes and evidence retained.",
        },
    )
    if failure:
        raise failure
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--readback")
    choice.add_argument("--checkpoint")
    args = parser.parse_args()
    with cold.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(
            run(
                args.readback or args.checkpoint,
                "readback" if args.readback else "checkpoint",
            )
        )


if __name__ == "__main__":
    main()
