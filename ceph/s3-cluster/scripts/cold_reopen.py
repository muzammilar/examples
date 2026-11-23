"""Full guest/kernel shutdown persistence; never an availability claim."""

import json
import shutil
import time
import uuid

import control as c
from native_state import identities


def main():
    active = c.run(
        [
            "docker",
            "ps",
            "-q",
            "--filter",
            "label=com.docker.compose.project=" + c.PROJECT,
            "--filter",
            "label=com.docker.compose.oneoff=True",
        ]
    ).split()
    if active:
        raise RuntimeError("owned workload/check containers still running")
    before = c.fixture_check()
    state = json.loads((c.BASE / "native-state.json").read_text())
    c.history()
    case = c.BASE / ("reopen-" + uuid.uuid4().hex)
    case.mkdir()
    c.save(case / "before.json", before)
    c.save(case / "native-before.json", state)
    started = time.monotonic()
    passed = False
    try:
        c.compose("stop", "--timeout", "45", "s3", "guests", timeout=120)
        stopped = c.inventory()
        if any(v["State"]["Running"] for v in stopped.values()):
            raise RuntimeError("full shutdown incomplete")
        c.save(case / "offline.json", stopped)
        c.compose("up", "-d", "--wait", "--wait-timeout", "900", timeout=960)
        after = c.inventory()
        fixture = json.loads((c.BASE / "fixture.json").read_text())
        if c.volumes(after) != fixture["volumes"]:
            raise RuntimeError("guest storage changed during retained restart")
        current = c.verify(c.BASE)
        if identities(current) != identities(state):
            raise RuntimeError("native cluster/OSDs changed during retained restart")
        for node, guest in state["guests"].items():
            if guest["boot_id"] == current["guests"][node]["boot_id"]:
                raise RuntimeError("guest kernel did not restart")
        for name in c.SERVICES:
            if (
                before[name]["Id"] != after[name]["Id"]
                or before[name]["State"]["StartedAt"]
                == after[name]["State"]["StartedAt"]
            ):
                raise RuntimeError("expected retained containers with new processes")
        for name in ["fixture", "native-state", "native-audit", "reconciliation"]:
            shutil.copy2(c.BASE / (name + ".json"), case / (name + ".json"))
        passed = True
    finally:
        if not passed:
            c.compose("up", "-d", "--wait", "--wait-timeout", "900", timeout=960)
        c.save(
            case / "result.json",
            {
                "passed": passed,
                "seconds": time.monotonic() - started,
                "scope": "full shutdown of three guest kernels; warm host caches; no HA claim",
            },
        )
    print((case / "result.json").read_text())


if __name__ == "__main__":
    main()
