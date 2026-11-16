"""Read the complete existing branch after recovery, without creating another copy."""

import json
import os
from pathlib import Path
import subprocess
import time

root = Path("results")
if "branch" in json.loads((root / "ids.json").read_text()):
    name = "branch-post-recovery-" + str(time.time_ns())
    subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "-T",
            "--entrypoint",
            "python",
            "runner",
            "-c",
            "import os, workload; workload.save("
            + repr(name)
            + ", workload.verify_dsn(os.environ['BRANCH_DSN']))",
        ],
        check=True,
        timeout=180,
    )
    report = json.loads((root / (name + ".json")).read_text())
    if report["rows"] != int(os.environ["ROWS"]) or any(
        report[key] for key in ("missing", "duplicate", "wrong_values")
    ):
        raise RuntimeError("branch recovery readback failed")
    print("Complete branch body readback passed:", report["rows"], "rows")
