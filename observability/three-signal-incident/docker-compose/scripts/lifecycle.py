"""Verified teardown and explicit retirement of this example's data generations."""

import hashlib
import json
import subprocess
import uuid

from lab import ROOT, PROJECT, BACKENDS, command, save, running_actors, owned


def witness_hashes():
    paths = set()
    for name in [
        "case.json",
        "fixture.json",
        "requests.jsonl",
        "configuration.json",
        "fenced.json",
    ]:
        paths.update((ROOT / "results/cases").rglob(name))
    return {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(paths)
    }


def is_retired(case):
    registry = ROOT / "results/retired-cases.json"
    if not registry.exists():
        return False
    receipt = json.loads(registry.read_text()).get(case.parent.name)
    if not receipt:
        return False
    evidence = json.loads((ROOT / receipt).read_text())
    if (
        case.parent.name not in evidence["cases"]
        or not evidence["owned_resources_absent"]
    ):
        raise RuntimeError("Retired fixture lacks completed cleanup evidence")
    for name, expected in evidence["witnesses"].items():
        source = ROOT / name
        if (
            not source.is_file()
            or hashlib.sha256(source.read_bytes()).hexdigest() != expected
        ):
            raise RuntimeError("Retired witness changed: " + name)
    return True


def remove(audit):
    if running_actors():
        raise RuntimeError("A workload actor is still running")
    audit()
    command("stop", *[role for roles in BACKENDS.values() for role in roles])
    plan = json.loads((ROOT / "results/plan.json").read_text())
    plan["state"] = "removed"
    save(ROOT / "results/plan.json", plan)
    save(
        ROOT / "results/verified-down.json",
        {
            "verified": True,
            "witnesses": witness_hashes(),
            "cases": [
                p.parent.name
                for p in sorted((ROOT / "results/cases").glob("*/case.json"))
                if not is_retired(p)
            ],
            "scope": "Acknowledged values/history reconciled before stopping; failed Victoria exact-type fidelity remains documented and is not changed by cleanup; volumes retained until docker-down",
        },
    )


def destroy():
    receipt_path = ROOT / "results/verified-down.json"
    evidence = json.loads(receipt_path.read_text())
    if not evidence["verified"] or evidence["witnesses"] != witness_hashes():
        raise RuntimeError("Missing or stale verified teardown receipt")
    tag = "obs-three-signal-incident-workload:local"
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
    ).strip()
    mounted = set()
    identifiers = subprocess.check_output(
        [
            "docker",
            "ps",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
        ],
        text=True,
    ).splitlines()
    for identifier in identifiers:
        metadata = json.loads(
            subprocess.check_output(["docker", "inspect", identifier], text=True)
        )[0]
        if metadata["Config"]["Labels"].get("com.docker.compose.project") != PROJECT:
            raise RuntimeError("Foreign pre-teardown container")
        mounted.update(m["Name"] for m in metadata["Mounts"] if m["Type"] == "volume")
    evidence["owned_container_mounted_volumes"] = sorted(mounted)
    command(
        "--profile",
        "greptime",
        "--profile",
        "victoria",
        "--profile",
        "grafana",
        "--profile",
        "tools",
        "down",
        "--volumes",
        "--remove-orphans",
    )
    for kind in ["container", "volume", "network"]:
        remaining = subprocess.check_output(
            [
                "docker",
                kind,
                "ls",
                "--quiet",
                "--filter",
                "label=com.docker.compose.project=" + PROJECT,
            ],
            text=True,
        )
        if remaining.strip():
            raise RuntimeError("Owned resources remain: " + kind)
    for volume in mounted:
        check = subprocess.run(
            ["docker", "volume", "inspect", volume], text=True, capture_output=True
        )
        if check.returncode == 0:
            raise RuntimeError(
                "A volume from an owned container remains; no full cleanup claim: "
                + volume
            )
    used = subprocess.check_output(
        ["docker", "ps", "--all", "--quiet", "--filter", "ancestor=" + image], text=True
    )
    if used.strip():
        raise RuntimeError("A container still uses the owned workload image")
    subprocess.run(["docker", "image", "rm", tag], check=True, capture_output=True)
    archive = ROOT / "results/teardowns" / uuid.uuid4().hex
    archive.mkdir(parents=True)
    evidence["owned_resources_absent"] = True
    evidence["removed_workload_image_id"] = image
    save(archive / "status.json", evidence)
    registry_path = ROOT / "results/retired-cases.json"
    registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    for case in evidence["cases"]:
        registry[case] = str((archive / "status.json").relative_to(ROOT))
    save(registry_path, registry)
    (ROOT / "results/plan.json").rename(archive / "plan.json")
    receipt_path.rename(archive / "verified-down.json")


def preserve_stop():
    """Stop only owned services; failed data cannot authorize deletion."""
    if running_actors():
        raise RuntimeError(
            "Live actors must stop before preserving the backend services"
        )
    roles = [role for group in BACKENDS.values() for role in group]
    command("stop", *roles)
    states = {}
    for role in roles:
        if command("ps", "--all", "--quiet", role).stdout.strip():
            row = owned(role)
            states[role] = {
                "id": row["Id"],
                "state": row["State"],
                "mounts": row["Mounts"],
            }
    save(
        ROOT / "results/preserved-stops" / (uuid.uuid4().hex + ".json"),
        {
            "verified_for_destruction": False,
            "witnesses": witness_hashes(),
            "services": states,
            "scope": "Stopped owned services only; failed histories/data volumes retained. This is not a passing audit or a teardown receipt.",
        },
    )
