"""Thin Make dispatcher; native operations live beside their owning tests."""

import sys
import time

from lab import ROOT, case, fixture, save
from lifecycle import bootstrap, destroy, initialize, remove


def main():
    mode = sys.argv[1]
    path = ROOT / "results" / (mode + "-" + str(time.time_ns()))
    path.mkdir(parents=True)
    if mode == "docker-up":
        bootstrap(path)
    elif mode == "up":
        initialize(path)
    elif mode == "resume-submission":
        from lifecycle import resume_submission

        resume_submission(path)
    elif mode == "test":
        from protocol import protocol_control
        from recovery import run_case

        protocol_control()
        for kind in ["connector-lifecycle", "backfill-recovery", "worker-recovery"]:
            run_case(kind)
    elif mode in ["connector-lifecycle", "backfill-recovery", "worker-recovery"]:
        from recovery import run_case

        run_case(mode)
    elif mode == "verify":
        from correctness import reconcile, settle
        from observations import resources

        run, evidence = case("retained-verification")
        settle(evidence)
        reconcile(evidence)
        resources(evidence)
        save(
            evidence / "status.json",
            {
                "passed": True,
                "kind": "retained-verification",
                "fixture": fixture()["id"],
                "run": run,
            },
        )
    elif mode == "down":
        remove(path)
    elif mode == "docker-down":
        destroy(path)
    else:
        raise ValueError(mode)


if __name__ == "__main__":
    main()
