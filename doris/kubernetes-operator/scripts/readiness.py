"""Wait for native usable backend capacity; Pod readiness is insufficient."""

import csv
import io
import json
from pathlib import Path
import time

import kube


def main():
    deadline = time.monotonic() + 120
    path = kube.ROOT / "results" / ("readiness-" + str(time.time_ns()) + ".jsonl")
    with path.open("x") as history:
        while time.monotonic() < deadline:
            result = {"utc": time.time(), "ready": False}
            try:
                raw = kube.query(
                    "SELECT COUNT(*) FROM backends() WHERE Alive = true AND TotalCapacity > 0 AND AvailCapacity > 1073741824"
                ).stdout
                rows = list(csv.reader(io.StringIO(raw), delimiter="\t"))
                result.update(
                    {"stdout": raw, "ready": bool(rows and rows[-1] == ["3"])}
                )
            except RuntimeError as error:
                text = str(error)
                if not any(
                    s in text
                    for s in (
                        "No available backends for compute group",
                        "[E-240]FE address is not available",
                        "Can't connect to MySQL server",
                    )
                ):
                    raise
                result["transient_error"] = text
            history.write(json.dumps(result) + "\n")
            history.flush()
            if result["ready"]:
                print("Native Doris reports three live backends with usable capacity")
                return
            time.sleep(1)
    raise TimeoutError(
        "No native three-backend capacity readiness within 120 seconds; do not seed"
    )


if __name__ == "__main__":
    main()
