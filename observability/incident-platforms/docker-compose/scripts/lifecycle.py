"""Reconcile preserved acknowledgements before stopping or deleting owned state."""

import hashlib
import json
import os
import subprocess
import uuid

from control import (
    ROOT,
    PROJECT,
    SERVICE,
    DOCKER,
    command,
    owned,
    save,
    retired,
    active_backend,
)
from experiments import fence, readback, running_actors

SERVICES = ["openobserve", "openobserve-sync"]


def cases(service):
    for metadata in sorted((ROOT / "results/cases").glob("*/case.json")):
        if (
            not retired(metadata.parent)
            and json.loads(metadata.read_text()).get("service", "openobserve")
            == service
        ):
            yield metadata.parent


def witnesses():
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for name in [
            "case.json",
            "fixture.json",
            "configuration.json",
            "requests.jsonl",
            "fenced.json",
        ]
        for path in sorted((ROOT / "results/cases").rglob(name))
    }


def stop_preserving():
    if running_actors():
        raise RuntimeError("Stop and reconcile live actors before stopping storage")
    for service in SERVICES:
        if command("ps", "--all", "--quiet", service).stdout.strip():
            owned(service)
    command("stop", *SERVICES)
    states = {}
    for service in SERVICES:
        if command("ps", "--all", "--quiet", service).stdout.strip():
            row = owned(service)
            states[service] = {
                "id": row["Id"],
                "state": row["State"],
                "mounts": row["Mounts"],
            }
    save(
        ROOT / "results/preserved-stops" / (uuid.uuid4().hex + ".json"),
        {
            "verified_for_destruction": False,
            "services": states,
            "witnesses": witnesses(),
            "scope": "Owned services stopped; all failed cases and data retained. Not a passing teardown.",
        },
    )


def remove():
    active_backend()
    if running_actors():
        raise RuntimeError("A live actor prevents teardown")
    destination = ROOT / "results/teardown-audits" / uuid.uuid4().hex
    for case in cases(SERVICE):
        directory = case / "openobserve"
        if not any(directory.glob("*/requests.jsonl")):
            continue
        # Teardown must not create a new fence around an interrupted publication.
        if not (directory / "fenced.json").exists():
            raise RuntimeError(
                "Unfenced publication requires explicit investigation: " + case.name
            )
        fence(directory)
        readback(case / "fixture.json", directory, destination / case.name)
    command("stop", SERVICE)
    row = owned(SERVICE)
    save(
        ROOT / "results/verified-down" / (SERVICE + ".json"),
        {
            "service": SERVICE,
            "container": row["Id"],
            "witnesses": witnesses(),
            "verified": True,
            "scope": "All acknowledged logical values reconciled; original failures remain documented",
        },
    )


def destroy():
    if running_actors():
        raise RuntimeError("A live actor prevents destruction")
    for service in SERVICES:
        if not command("ps", "--all", "--quiet", service).stdout.strip():
            if any(cases(service)):
                raise RuntimeError(
                    "Preserved cases lack their owned backend container: " + service
                )
            volume = subprocess.run(
                [*DOCKER, "volume", "inspect", PROJECT + "_" + service],
                capture_output=True,
            )
            if volume.returncode == 0:
                raise RuntimeError(
                    "Unverified detached volume must be preserved: " + service
                )
            continue
        row = owned(service)
        receipt = ROOT / "results/verified-down" / (service + ".json")
        if not receipt.exists():
            raise RuntimeError(
                "No verified teardown receipt for " + service + "; preserve failed data"
            )
        value = json.loads(receipt.read_text())
        if (
            not value["verified"]
            or value["container"] != row["Id"]
            or value["witnesses"] != witnesses()
            or row["State"]["Running"]
        ):
            raise RuntimeError("Stale teardown receipt or running backend: " + service)
        for mount in row["Mounts"]:
            if mount["Type"] == "volume":
                metadata = json.loads(
                    subprocess.check_output(
                        [*DOCKER, "volume", "inspect", mount["Name"]], text=True
                    )
                )[0]
                if (
                    metadata.get("Labels", {}).get("com.docker.compose.project")
                    != PROJECT
                ):
                    raise RuntimeError("Foreign volume prevents destruction")
    active_cases = [case for service in SERVICES for case in cases(service)]
    command(
        "--profile",
        "openobserve",
        "--profile",
        "openobserve-buffered",
        "--profile",
        "tools",
        "down",
        "--volumes",
    )
    for kind in ["container", "volume", "network"]:
        remaining = subprocess.check_output(
            [
                *DOCKER,
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
    receipt = ROOT / "results/teardowns" / (uuid.uuid4().hex + ".json")
    save(receipt, {"owned_resources_absent": True, "witnesses": witnesses()})
    registry = ROOT / "results/retired-cases.json"
    values = json.loads(registry.read_text()) if registry.exists() else {}
    values.update({case.name: str(receipt.relative_to(ROOT)) for case in active_cases})
    save(registry, values)


def execute(mode):
    if mode == "preserve-stop":
        stop_preserving()
    elif mode == "down":
        remove()
    elif mode == "docker-down":
        destroy()
    else:
        from control import driver_control, verify_control

        controls = [
            case
            for case in cases(SERVICE)
            if json.loads((case / "case.json").read_text())["kind"] == "driver-control"
        ]
        if not controls:
            if mode != "up":
                raise RuntimeError("Run make up first")
            driver_control()
            controls = list(cases(SERVICE))
        os.environ["CASE"] = controls[-1].name
        verify_control()
