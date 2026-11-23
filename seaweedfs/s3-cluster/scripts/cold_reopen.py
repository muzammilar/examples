"""Controlled full shutdown: persistence and restart, with no HA claim."""

import shutil
import time
import uuid

import control as c


def main():
    before = c.fixture_check()
    c.history()
    case = c.BASE / ("reopen-" + uuid.uuid4().hex)
    case.mkdir()
    c.save(case / "before.json", before)
    # No controller may overlap this full-stack operation.
    ids = c.compose("ps", "-q", "tools").split()
    if ids:
        raise RuntimeError("workload tools are still running")
    stopped = False
    success = False
    started = time.monotonic()
    try:
        stopped = True
        c.compose("stop", "--timeout", "30", *sorted(c.SERVICES), timeout=180)
        offline = c.inventory()
        if any(v["State"]["Running"] for v in offline.values()):
            raise RuntimeError("full shutdown did not stop every owned process")
        c.save(case / "offline.json", offline)
        c.compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=360)
        after = c.fixture_check()
        for name in c.SERVICES:
            if (
                after[name]["Id"] != before[name]["Id"]
                or after[name]["State"]["StartedAt"]
                == before[name]["State"]["StartedAt"]
            ):
                raise RuntimeError(
                    "expected retained container with a restarted process: " + name
                )
        c.verify(c.BASE)
        c.save(case / "after.json", after)
        for name in [
            "fixture",
            "reconciliation",
            "physical-recovery",
            "replica-byte-audit",
            "metadata-audit",
        ]:
            shutil.copy2(c.BASE / (name + ".json"), case / (name + ".json"))
        success = True
    finally:
        if stopped and not success:
            c.compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=360)
        c.save(
            case / "result.json",
            {
                "passed": success,
                "seconds": time.monotonic() - started,
                "retained_containers": len(c.SERVICES),
                "scope": "full shutdown; no workload or availability claim",
            },
        )
    print((case / "result.json").read_text(), end="")


if __name__ == "__main__":
    main()
