"""Native constraint/missing-key controls, followed by independent body readback."""

import fcntl
import json
import subprocess
import uuid

import control


def main():
    with (control.RESULTS / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        control.owned_inventory()
        lab = json.loads((control.RESULTS / "lab.json").read_text())
        results = {}
        for backend in control.BACKENDS:
            rejected = None
            try:
                control.app(
                    backend,
                    "sql",
                    "INSERT INTO orders VALUES (0,0,0,'duplicate must not replace the old body')",
                )
            except subprocess.CalledProcessError as error:
                rejected = {
                    "stdout": error.stdout,
                    "stderr": error.stderr,
                    "returncode": error.returncode,
                }
            if rejected is None or "23505" not in rejected["stderr"]:
                raise RuntimeError(
                    "duplicate primary key was not rejected with SQLSTATE 23505"
                )
            missing = control.app(backend, "sql", "SELECT id FROM orders WHERE id=-1")
            if any(isinstance(row, list) for row in missing):
                raise RuntimeError("missing-key lookup returned a row")
            readback = control.reconcile(backend)
            results[backend] = {
                "duplicate_rejected": rejected,
                "missing_key": missing,
                "unchanged_full_body": {
                    k: v
                    for k, v in readback.items()
                    if k not in {"observed_live_ids", "export_process"}
                },
            }
        identity = uuid.uuid4().hex
        control.save(
            control.RESULTS / "driver-controls" / (identity + ".json"),
            {"passed": True, "generation": lab["generation"], "systems": results},
        )
        print(
            "Native duplicate/missing-key controls and full readback passed", identity
        )


if __name__ == "__main__":
    main()
