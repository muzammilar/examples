"""Recompute traffic from raw journals and require a current all-copy proof."""

import json
from collections import Counter

import control
from metrics import summarize
from version_history import digest


def validate_proof(fixture, witness, proof, expected_rows):
    copies = 1 if fixture["mode"] == "single" else 3
    groups = 3 if fixture["mode"] == "sharded" else 1
    endpoints = {f"http://ts{i}:8108" for i in range(1, copies * groups + 1)}
    if (
        proof["fixture_id"] != fixture["id"]
        or proof["mode"] != fixture["mode"]
        or proof["fixture_sha256"] != fixture["corpus_sha256"]
        or proof["acknowledgement_floor_sha256"] != digest(witness.value())
        or set(proof["copies"]) != endpoints
        or set(proof["native_before"]) != endpoints
        or set(proof["native_after"]) != endpoints
        or proof["finished_at"] <= 0
    ):
        raise RuntimeError(
            "proof is stale or does not cover this fixture/topology/history"
        )
    for shard in range(groups):
        peers = [
            f"http://ts{i}:8108" for i in range(shard * 3 + 1, shard * 3 + copies + 1)
        ]
        for label in ["native_before", "native_after"]:
            if sum(proof[label][peer]["state"] == "LEADER" for peer in peers) != 1:
                raise RuntimeError("native witness lacks exactly one leader per group")
            for peer in peers:
                value = proof[label][peer]
                if (
                    value["shard"] != shard
                    or value["state"] not in {"LEADER", "FOLLOWER"}
                    or value["committed_index"]
                    < proof["committed_watermarks"][str(shard)]
                    or (label == "native_before" and value["queued_writes"] != 0)
                ):
                    raise RuntimeError("native witness does not establish catch-up")
    for endpoint, value in proof["copies"].items():
        expected_shard = (
            (int(endpoint.split("ts")[1].split(":")[0]) - 1) // 3 if groups == 3 else 0
        )
        if (
            value["shard"] != expected_shard
            or value["rows"] != expected_rows[expected_shard]
            or value["vector_coordinates"] != value["rows"] * 384
            or any(
                value[key] != 0
                for key in [
                    "missing_ids",
                    "stale_versions",
                    "wrong_payloads",
                    "misplaced_ids",
                ]
            )
        ):
            raise RuntimeError(
                "copy proof contains missing, stale, corrupt or misplaced data"
            )
    if (
        proof["rows_checked"] != sum(v["rows"] for v in proof["copies"].values())
        or proof["vector_coordinates_checked"] != proof["rows_checked"] * 384
    ):
        raise RuntimeError("copy proof totals are inconsistent")


def main():
    fixture = control.read(control.BASE / "fixture.json")
    witness = control.history()
    proof = control.read(control.BASE / "replica-proof.json")
    counts = Counter()
    with (control.ROOT / "results/corpus/prepared/products.jsonl").open() as stream:
        for line in stream:
            row = json.loads(line)
            counts[row["tenant"] % 3 if fixture["mode"] == "sharded" else 0] += 1
    validate_proof(fixture, witness, proof, counts)
    cases = []
    for case in sorted(control.BASE.glob("fault-*")):
        metadata = control.read(case / "case.json")
        if metadata["fixture_id"] != fixture["id"]:
            raise RuntimeError("fault history mixes fixtures")
        status = control.read(case / "status.json")
        commands = metadata.get("native_commands")
        if commands is None:
            before = control.read(case / "before.json")
            commands = {name: value["Config"]["Cmd"] for name, value in before.items()}
        read_lags = {
            name: next(
                (
                    int(arg.split("=", 1)[1])
                    for arg in args
                    if arg.startswith("--healthy-read-lag=")
                ),
                1000,
            )
            for name, args in commands.items()
        }
        cases.append(
            {
                "case": case.name,
                "kind": metadata["kind"],
                "shard": metadata["shard"],
                "target": metadata["target"],
                "status": status,
                "healthy_read_lag_by_node": read_lags,
                "traffic": summarize(case),
            }
        )
    reopens = []
    for case in sorted(control.BASE.glob("reopen-*")):
        recovery = case / "eventual-recovery.json"
        reopens.append(
            {
                "case": case.name,
                "result": control.read(case / "result.json"),
                "eventual_recovery": control.read(recovery)
                if recovery.exists()
                else None,
            }
        )
    result = {
        "fixture_id": fixture["id"],
        "mode": fixture["mode"],
        "acknowledged_rows": witness.acknowledgements,
        "attempted_rows": witness.operations,
        "rows_checked": proof["rows_checked"],
        "vector_coordinates_checked": proof["vector_coordinates_checked"],
        "cases": cases,
        "reopen_cases": reopens,
        "all_recorded_fault_cases_passed": bool(cases)
        and all(c["status"]["passed"] for c in cases),
        "limits": "Local API copy proof; single Docker host; rate-limited faults are not a capacity benchmark. Failed experiments remain failed.",
    }
    control.save(control.BASE / "report.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    with control.controller_lock():
        main()
