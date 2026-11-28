"""One healthy input fixture shared by the native profiles in this comparison."""

import json
import time
import uuid

from control import ROOT, save


def healthy_fixture():
    plan = ROOT / "results/corpus-plan.json"
    if not plan.exists():
        fixture = None
        origin = "fresh shared comparison fixture"
        for metadata in sorted((ROOT / "results/cases").glob("*/case.json")):
            case = json.loads(metadata.read_text())
            status = metadata.parent / "status.json"
            if (
                case.get("service") != "openobserve-sync"
                or case["kind"] != "healthy"
                or not status.exists()
            ):
                continue
            if not json.loads(status.read_text()).get("common_projection_passed"):
                continue
            candidate = json.loads((metadata.parent / "fixture.json").read_text())
            if 0 < time.time() * 1000 - candidate["start_ms"] < 86400000:
                fixture, origin = candidate, str(metadata.parent.relative_to(ROOT))
                break
        if fixture is None:
            fixture = {
                "run": uuid.uuid4().hex,
                "payload_seed": "b4587bd2c2314cc7a7c4062660ad874c",
                "start_ms": int(time.time() - 7200) * 1000,
                "requests": 10000,
                "logs_per_request": 100,
                "metric_points": 100,
            }
        save(
            plan,
            {
                "fixture": fixture,
                "origin": origin,
                "scope": "Healthy native profiles reuse exact event IDs, values and timestamps; fault runs have their own separately witnessed IDs",
            },
        )
    fixture = json.loads(plan.read_text())["fixture"]
    if (fixture["requests"], fixture["logs_per_request"], fixture["metric_points"]) != (
        10000,
        100,
        100,
    ):
        raise RuntimeError("Shared fixture shape differs")
    if not 0 < time.time() * 1000 - fixture["start_ms"] < 6 * 86400000:
        raise RuntimeError(
            "Shared fixture is outside the bounded retention window; preserve old state rather than silently regenerating input"
        )
    return fixture
