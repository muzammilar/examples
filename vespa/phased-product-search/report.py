"""Recompute outcomes from all durable journals, including post-abort shutdown events."""

import json
import fcntl

from control import BASE, ROOT, history, read, save
from version_history import History, digest, records
from metrics import summarize


def main():
    retained = history()
    cases = []
    for metadata in sorted(
        (BASE / "cases").glob("*/case.json"),
        key=lambda p: (
            read(p)["start_version"],
            read(p).get("created_at", p.stat().st_mtime),
        ),
    ):
        directory = metadata.parent
        original = (
            read(directory / "report.json")
            if (directory / "report.json").exists()
            else {"status": "incomplete"}
        )
        result = {
            "case": directory.name,
            "status": original["status"],
            **summarize(directory),
        }
        if "error" in original:
            result["error"] = original["error"]
        if original["status"] == "passed":
            current = History(read(directory / "history.json"))
            for event in records(directory / "writer.jsonl"):
                current.append(event)
            proof = read(directory / "final-proof.json")
            if proof["floor_sha256"] != digest(current.value()):
                raise RuntimeError("proof does not cover the complete case witness")
            if any(proof[key] for key in ["missing", "stale", "wrong_payloads"]):
                raise RuntimeError("invalid passing proof")
            result["native_rows_checked"] = proof["rows_checked"]
        cases.append(result)
    value = {
        "cases": cases,
        "acknowledged_updates": sum(c["writer_rows"]["ack"] for c in cases),
        "attempted_updates": sum(c["writer_rows"]["attempt"] for c in cases),
        "ambiguous_updates": sum(c["writer_rows"]["ambiguous"] for c in cases),
        "distinct_acknowledged_ids": len(retained["ack_versions"]),
        "passed_cases": sum(c["status"] == "passed" for c in cases),
        "failed_cases": sum(c["status"] == "failed" for c in cases),
        "latest_case_reconciled": bool(cases) and cases[-1]["status"] == "passed",
        "scope": "raw complete histories, not a substitute for native replica proof",
    }
    save(BASE / "aggregate.json", value)
    print(json.dumps({k: v for k, v in value.items() if k != "cases"}), flush=True)


if __name__ == "__main__":
    with (ROOT / "results/controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(
                "A controller is active; wait for its journals to close before reporting"
            )
        main()
