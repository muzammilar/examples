"""Publish an explicit matched saved-snapshot pair, without capacity/HA claims."""

import argparse
import hashlib
import json
from pathlib import Path

import experiment as bench
import ingestion


def native_file(engine, name):
    return (
        name.startswith(("trades/", "customers/"))
        if engine == "rayforce"
        else name.startswith("state.duckdb")
    )


def publish(paths):
    cases = []
    for path in paths:
        path = path.resolve(strict=True)
        if not path.is_relative_to(bench.RESULTS.resolve()):
            raise ValueError("result must be inside this example's results")
        case = json.loads(path.read_text())
        if (
            not case["passed"]
            or not case["fixture"]
            or not case["dimension_verification"]
        ):
            raise RuntimeError("complete trade/customer proof is required")
        if not case.get("verified_export_removed"):
            raise RuntimeError(
                "retain durable verification proof before removing exports"
            )
        for name, digest in case["source_sha256"].items():
            if hashlib.sha256((bench.ROOT / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("tested source differs: " + name)
        for record in case["records"]:
            if (
                record["cpus"] != 1
                or record["memory_limit_bytes"] != 4 * 1024**3
                or record["memory_swap_limit_bytes"] != 4 * 1024**3
            ):
                raise RuntimeError("snapshot resource budgets differ")
        engine = case["records"][0]["engine"]
        samples = [s for r in case["records"] for s in r["owned_storage_samples"]]
        case["sampled_native_file_bytes_max"] = max(
            sum(
                v["bytes"]
                for name, v in s["files"].items()
                if native_file(engine, name)
            )
            for s in samples
        )
        case["raw_native_file_samples_scope"] = (
            "Non-atomic directory observations can miss peaks or race renames; raw per-file values and known races remain in each record. Export witnesses are excluded from the native-file sum."
        )
        case["source_result"] = str(path.relative_to(bench.ROOT))
        cases.append(case)
    if {c["records"][0]["engine"] for c in cases} != {"rayforce", "duckdb"}:
        raise RuntimeError("one case per engine required")
    if (
        cases[0]["fixture"] != cases[1]["fixture"]
        or cases[0]["environment"] != cases[1]["environment"]
    ):
        raise RuntimeError("input/environment differ")
    if len({r["image"] for c in cases for r in c["records"]}) != 1:
        raise RuntimeError("different workload images")
    for case in cases:
        if (
            case["verification"]["sha256"] != case["fixture"]["sha256"]["trades.csv"]
            or case["dimension_verification"]["sha256"]
            != case["fixture"]["sha256"]["customers.csv"]
        ):
            raise RuntimeError("snapshot differs from the independent input fixture")
    result = {
        "scope": "One matched saved-snapshot correctness/cost observation per engine, not a repeated capacity benchmark, live backup, crash recovery or power-loss test.",
        "controller_tests": 17,
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cases": cases,
        "unrun": [
            "live journal recovery under independent writers/readers",
            "SIGKILL, torn journal and interior corruption",
            "snapshot replacement interrupted before/during publication",
            "second-writer/fencing and supported backup restore",
            "repeated performance matrix and genuinely OS-cold queries",
        ],
    }
    ingestion.save(bench.ROOT / "projects/snapshots/reports/PERSISTENCE_RESULTS.json", result)
    print("Published two saved-snapshot profiles; live recovery remains unrun.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "results",
        nargs=2,
        type=Path,
        help="Explicit Rayforce and DuckDB result.json paths",
    )
    publish(parser.parse_args().results)
