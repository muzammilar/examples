"""Fail closed on unknown native state and verify actual controller role selection."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import control
from metrics import summarize


def healthy():
    nodes = {str(i): {"state": {"generated": {"state": "up"}}} for i in range(3)}
    metrics = {
        name: {
            "nodes": [
                {
                    "services": [
                        {
                            "metrics": [
                                {
                                    "values": {
                                        "vds.idealstate.merge_bucket.pending.average": 0,
                                        "content.proton.documentdb.documents.total.last": 100000,
                                        "content.proton.documentdb.documents.ready.last": 100000,
                                    }
                                }
                            ]
                        }
                    ]
                }
            ]
        }
        for name in ["node0", "node1", "node2"]
    }
    return {
        "native": {
            "endpoint": "http://vespa-products-node2.vespa-product-search-net:19050/cluster/v2/catalog",
            "state": {
                "service": {
                    "storage": {"node": deepcopy(nodes)},
                    "distributor": {"node": deepcopy(nodes)},
                },
                "metrics": {"cluster-buckets-out-of-sync-ratio": 0},
            },
        },
        "metrics": metrics,
    }


class NativeGateTests(unittest.TestCase):
    def test_each_fault_checks_retained_proof_before_starting_a_case(self):
        for action in ["crash", "disk", "rebuilt-crash", "cold-reopen"]:
            with self.subTest(action=action):
                with (
                    patch.object(control, "binding"),
                    patch.object(
                        control, "history", return_value={"witness": "retained"}
                    ),
                    patch.object(
                        control,
                        "require_reconciled_witness",
                        side_effect=RuntimeError("incomplete witness"),
                    ) as guard,
                    patch.object(control, "compose") as compose,
                ):
                    with self.assertRaisesRegex(RuntimeError, "incomplete witness"):
                        control.case(action, "node1")
                    guard.assert_called_once_with({"witness": "retained"})
                    compose.assert_not_called()

    def test_benchmark_rejects_an_incomplete_case_after_an_older_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, version in [("old", 1), ("new", 2)]:
                case = root / "cases" / name
                case.mkdir(parents=True)
                (case / "case.json").write_text(
                    json.dumps({"start_version": version, "created_at": version})
                )
            (root / "cases/old/report.json").write_text(
                json.dumps({"status": "passed"})
            )
            with (
                patch.object(control, "BASE", root),
                patch.object(control, "binding"),
                patch.object(control.sys, "argv", ["control.py", "benchmark"]),
                patch.object(control.subprocess, "run") as run,
            ):
                with self.assertRaisesRegex(RuntimeError, "qualify the fixture"):
                    control.main()
                run.assert_not_called()

    def test_docker_up_dispatch_never_enters_application_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(control, "ROOT", root),
                patch.object(control, "inspect", return_value={}),
                patch.object(control, "compose") as compose,
                patch("corpus.main"),
                patch("prepare_build.prepare"),
                patch.object(control.sys, "argv", ["control.py", "docker-up"]),
            ):
                control.main()
            self.assertEqual(
                [call.args[0] for call in compose.call_args_list], ["build", "up"]
            )
            self.assertEqual(
                json.loads((root / "results/infrastructure.json").read_text())["mode"],
                "cluster",
            )

    def test_repair_requires_every_copy_ready_and_every_merge_queue_empty(self):
        baseline = healthy()
        with patch.object(
            control, "read", return_value={"parameters": {"documents": 100000}}
        ):
            self.assertTrue(control.repair_ready(baseline))
            for key, bad_value in [
                ("vds.idealstate.merge_bucket.pending.average", 1),
                ("content.proton.documentdb.documents.ready.last", 99999),
                ("content.proton.documentdb.documents.total.last", None),
            ]:
                bad = deepcopy(baseline)
                bad["metrics"]["node1"]["nodes"][0]["services"][0]["metrics"][0][
                    "values"
                ][key] = bad_value
                self.assertFalse(control.repair_ready(bad))
            bad = deepcopy(baseline)
            bad["native"]["state"]["metrics"]["cluster-buckets-out-of-sync-ratio"] = (
                0.01
            )
            self.assertFalse(control.repair_ready(bad))
            bad = deepcopy(baseline)
            bad["native"]["state"]["service"]["storage"]["node"]["0"]["state"][
                "generated"
            ]["state"] = "down"
            self.assertFalse(control.repair_ready(bad))

    def test_reporting_failure_still_leaves_a_failed_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(
                control, "summarize", side_effect=KeyError("missing sample")
            ):
                control.failed_report(root, RuntimeError("original native failure"))
            value = json.loads((root / "report.json").read_text())
            self.assertEqual(value["status"], "failed")
            self.assertEqual(value["error"], "original native failure")
            self.assertIn("missing sample", value["reporting_error"])

    def test_new_fault_requires_a_proof_covering_every_retained_ack(self):
        from version_history import digest, empty

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "cases/one"
            case.mkdir(parents=True)
            (case / "case.json").write_text(
                json.dumps({"start_version": 1, "created_at": 1})
            )
            proof = {
                "floor_sha256": digest(empty()),
                "all_native_versions_agree": True,
                "missing": 0,
                "stale": 0,
                "wrong_payloads": 0,
            }
            (case / "final-proof.json").write_text(json.dumps(proof))
            with patch.object(control, "BASE", root):
                control.require_reconciled_witness(empty())
                changed = empty()
                changed["attempted_versions"] = {"0": [1]}
                with self.assertRaisesRegex(RuntimeError, "complete converged witness"):
                    control.require_reconciled_witness(changed)
                proof["all_native_versions_agree"] = False
                (case / "final-proof.json").write_text(json.dumps(proof))
                with self.assertRaises(RuntimeError):
                    control.require_reconciled_witness(empty())

    def test_diagnostic_hits_have_counts_without_invented_latency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            events = [
                {"stage": "partial_search_hit", "time": 2},
                {"stage": "stale_search_hit", "time": 3},
                {"stage": "search", "time": 4, "seconds": 0.01},
            ]
            (root / "reader.jsonl").write_text(
                "".join(json.dumps(e) + "\n" for e in events)
            )
            (root / "phases.jsonl").write_text(
                "".join(
                    json.dumps({"phase": name, "time": time}) + "\n"
                    for name, time in [("baseline", 1), ("verified", 5)]
                )
            )
            value = summarize(root)["phases"]["baseline"]
            self.assertEqual(
                value["partial_search_hit"],
                {"count": 1, "observations_per_second": 0.25},
            )
            self.assertEqual(value["search"]["p99_ms"], 10)

    def test_missing_cluster_metric_is_unknown_not_zero(self):
        bad = healthy()
        bad["native"]["state"].pop("metrics")
        self.assertFalse(control.repair_ready(bad))

    def test_controller_is_discovered_from_native_redirect_target(self):
        self.assertEqual(control.active_controller(healthy()), "node2")
        bad = healthy()
        bad["native"]["endpoint"] = "http://foreign.example:19050/"
        with self.assertRaises(RuntimeError):
            control.active_controller(bad)

    def test_unknown_runtime_does_not_destroy_a_volume(self):
        before = {
            "node0": {
                "Id": "old",
                "Mounts": [
                    {"Type": "volume", "Destination": "/opt/vespa/var", "Name": "owned"}
                ],
            }
        }
        volume = {"Labels": {"com.docker.compose.project": control.PROJECT}}
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(control, "run", return_value=json.dumps([volume])) as run,
                patch.object(
                    control, "inspect", side_effect=RuntimeError("daemon unreachable")
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "daemon unreachable"):
                    control.replace_disk(Path(directory), "node0", before)
            self.assertEqual(
                run.call_args_list[0].args[0], ["docker", "volume", "inspect", "owned"]
            )
            self.assertEqual(len(run.call_args_list), 1)

    def test_live_old_owner_does_not_destroy_a_volume(self):
        old = {
            "Id": "old",
            "Mounts": [
                {"Type": "volume", "Destination": "/opt/vespa/var", "Name": "owned"}
            ],
        }
        volume = {"Labels": {"com.docker.compose.project": control.PROJECT}}
        with tempfile.TemporaryDirectory() as directory:
            with (
                patch.object(control, "run", return_value=json.dumps([volume])) as run,
                patch.object(
                    control,
                    "inspect",
                    return_value={"node0": old | {"State": {"Running": True}}},
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "not positively fenced"):
                    control.replace_disk(Path(directory), "node0", {"node0": old})
            self.assertEqual(len(run.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
