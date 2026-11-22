"""Publish explicit functional cases and a short harness diagnostic, without ranking engines."""

import argparse
import hashlib
import json
from pathlib import Path

import control
import mutable_oracle
from report import native_values


def load(path):
    return json.loads(path.read_text())


def publish(paths, cohort, failures):
    cases, coverage, images = [], set(), set()
    for path in paths:
        root = path.resolve(strict=True)
        if not root.is_relative_to((control.RESULTS / "mutable").resolve()):
            raise ValueError("select an example-owned mutable case")
        config, result = load(root / "config.json"), load(root / "result.json")
        if (
            not result["passed"]
            or (root / "failure.json").exists()
            or (root / "invalidated.json").exists()
        ):
            raise RuntimeError("case failed or was invalidated")
        if not load(root / "cleanup.json")["schema_dropped_after_verification"]:
            raise RuntimeError("case cleanup is incomplete")
        for name, digest in config["source_sha256"].items():
            if hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest() != digest:
                raise RuntimeError("tested source differs: " + name)
        proof = mutable_oracle.reconcile(
            root / "rows.csv",
            root / "receipts.csv",
            root / "writer.jsonl",
            config["rows"],
            config["pairs"],
            root / "reader.jsonl",
        )
        if proof != {
            key: value for key, value in result["proof"].items() if key != "compression"
        }:
            raise RuntimeError("full-body witness proof differs")
        key = config["backend"], result["crash"]
        if key in coverage:
            raise RuntimeError("duplicate qualification cell")
        coverage.add(key)
        actors = {}
        for role in ("writer", "reader"):
            info = load(root / (role + "-stopped.json"))
            if info["State"]["Running"] or info["State"]["ExitCode"]:
                raise RuntimeError("actor did not exit cleanly")
            if info["HostConfig"]["NanoCpus"] != 10**9 or any(
                info["HostConfig"][field] != 512 * 1024**2
                for field in ("Memory", "MemorySwap")
            ):
                raise RuntimeError("actor budget differs")
            images.add(info["Image"])
            records = [
                json.loads(line)
                for line in (root / (role + ".log")).read_text().splitlines()
            ]
            resources = [
                record
                for record in records
                if record.get("kind") == "process-resources"
            ]
            if len(resources) != 1:
                raise RuntimeError("missing native Go resource counters")
            events = control.journal(root / (role + ".jsonl"))
            operations = {}
            for operation in sorted(
                {
                    event.get("operation", "")
                    for event in events
                    if event["kind"] != "intent"
                }
            ):
                selected = [
                    event
                    for event in events
                    if event["kind"] != "intent"
                    and event.get("operation", "") == operation
                ]
                operations[operation] = control.phase_summary(
                    selected,
                    min(event["started_ns"] for event in selected),
                    max(event["ended_ns"] for event in selected),
                )
            actors[role] = {"native_process": resources[0], "operations": operations}
        if not all(
            actors["writer"]["operations"].get(name, {}).get("ok", {}).get("count", 0)
            > 0
            for name in ("paired-update", "delete-reinsert")
        ):
            raise RuntimeError("a declared mutation operation was never successful")
        if result["crash"]:
            dead = load(root / "killed.json")
            if dead["State"]["Running"] or dead["State"]["ExitCode"] != 137:
                raise RuntimeError("missing native crash proof")
        controls = [
            record
            for record in load(root / "negative-controls.json")
            if record.get("kind") == "mutable-negative-controls"
        ]
        if len(controls) != 1 or not controls[0]["passed"]:
            raise RuntimeError("native read-only/rollback controls missing")
        cases.append(
            {
                "source_directory": str(root.relative_to(control.ROOT)),
                "config": config,
                "backend": key[0],
                "crash": key[1],
                "proof": result["proof"],
                "phases": result["phases"],
                "resources": result["resources"],
                "actors": actors,
                "native_after": native_values(result["native_after"]),
                "native_controls": controls[0],
                "raw_result_sha256": hashlib.sha256(
                    (root / "result.json").read_bytes()
                ).hexdigest(),
            }
        )
    if (
        coverage
        != {(backend, crash) for backend in control.BACKENDS for crash in (False, True)}
        or len(images) != 1
    ):
        raise RuntimeError(
            "require one healthy and one crash cell per backend on one workload image"
        )
    cohort = cohort.resolve(strict=True)
    if not cohort.is_relative_to((control.RESULTS / "mutable-benchmark").resolve()):
        raise ValueError("select an owned diagnostic cohort")
    raw = load(cohort / "results.json")
    if (
        not raw.get("finalized")
        or not raw["complete"]
        or not raw["config"]["diagnostic_only"]
    ):
        raise RuntimeError(
            "this checkpoint accepts only a completed short diagnostic, not comparative benchmark claims"
        )
    if (
        {(c["backend"], c["concurrent"]) for c in raw["cells"]}
        != {(b, v) for b in control.BACKENDS for v in (False, True)}
        or len(raw["cells"]) != 4
        or any(not c["passed"] for c in raw["cells"])
    ):
        raise RuntimeError("diagnostic matrix incomplete")
    for name, digest in raw["config"]["source_sha256"].items():
        if hashlib.sha256((control.ROOT / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError("diagnostic source changed: " + name)
    preserved = {}
    for path in failures:
        path = path.resolve(strict=True)
        if not path.is_relative_to((control.RESULTS / "mutable").resolve()):
            raise ValueError("select an owned failed/invalidated mutable case")
        if (path / "failure.json").exists():
            status = {"status": "FAILED", "result": load(path / "failure.json")}
            if (path / "diagnosis.json").exists():
                status["diagnosis"] = load(path / "diagnosis.json")
        elif (path / "invalidated.json").exists():
            status = {
                "status": "INVALIDATED",
                "result": load(path / "invalidated.json"),
            }
        else:
            raise RuntimeError(
                "referenced historical failure has no failed/invalidated receipt"
            )
        preserved[str(path.relative_to(control.ROOT))] = status
    report = {
        "scope": "Functional/recovery qualification and short four-cell benchmark-harness diagnostic, not sustained million-row or comparative performance qualification",
        "workload_image": next(iter(images)),
        "cases": cases,
        "diagnostic_cohort": {
            "source_directory": str(cohort.relative_to(control.ROOT)),
            "config": raw["config"],
            "cells": [
                {
                    key: c[key]
                    for key in ("backend", "concurrent", "identity", "verification")
                }
                for c in raw["cells"]
            ],
            "raw_result_sha256": hashlib.sha256(
                (cohort / "results.json").read_bytes()
            ).hexdigest(),
        },
        "sustained_million_row_comparison": "PENDING: 3 trials per backend/traffic condition, 60s warmup and >=300s measurement",
        "preserved_failures": preserved,
        "limits": [
            "single Docker VM, no HA/power-loss/independent-disk proof",
            "one writer, no multi-writer conflict qualification",
            "whole-store bytes include the immutable base fixture and retained prototype-failure schema",
            "resource counter reset intervals excluded; CPU observations are not complete totals across a crash",
            "PID 1 RSS is not whole-database memory; use cgroups",
            "native compression covers cold data where exposed; CSV gzip is separate",
            "block I/O is VM/kernel accounting; physical device latency/queue depth unavailable",
            "snapshot reads combine recent and historical resident rows, not isolated hot/warm/cold read benchmarks",
        ],
        "publisher_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    control.save(control.ROOT / "MUTABLE_RESULTS.json", report)
    print(
        "Published mutable functional/recovery evidence; sustained benchmark remains pending"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs=4, type=Path, required=True)
    parser.add_argument("--diagnostic-cohort", type=Path, required=True)
    parser.add_argument("--failures", nargs="*", type=Path, default=[])
    args = parser.parse_args()
    publish(args.cases, args.diagnostic_cohort, args.failures)
