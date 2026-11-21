import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import enrichment_control as control
import enrichment_lab as lab
import enrichment_metrics as metrics
import enrichment_probes as probes


class NativeHealthTests(unittest.TestCase):
    def test_rejects_single_copy_internal_topics(self):
        with (
            patch.object(lab, "service", return_value={"State": {"Running": True}}),
            patch.object(
                lab,
                "cli",
                return_value="Topic: connect-configs Partition: 0 Leader: 4 Replicas: 4 Isr: 4",
            ),
        ):
            self.assertIsNone(control.native())

    def test_compose_uses_image_supported_worker_settings(self):
        import subprocess

        specification = json.loads(
            subprocess.check_output(
                lab.COMPOSE + ["config", "--format", "json"],
                cwd=lab.ROOT,
                env=lab.environment(),
                text=True,
            )
        )
        for role in ["connect-1", "connect-2"]:
            settings = specification["services"][role]["environment"]
            self.assertEqual(settings["ADVERTISED_HOST_NAME"], role)
            self.assertEqual(str(settings["OFFSET_FLUSH_INTERVAL_MS"]), "1000")
            for kind in ["CONFIG", "OFFSET", "STATUS"]:
                self.assertEqual(
                    str(settings["CONNECT_" + kind + "_STORAGE_REPLICATION_FACTOR"]),
                    "3",
                )


class ResourceTests(unittest.TestCase):
    def test_io_deltas_and_cpu_utilization_do_not_include_previous_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.gz"
            with gzip.open(path, "wt") as stream:
                for timestamp, cpu, read, write, memory in [
                    (10, 4_000_000_000, 1000, 2000, 100),
                    (12, 5_000_000_000, 1128, 2256, 200),
                ]:
                    stream.write(
                        json.dumps(
                            {
                                "id": "member",
                                "start": "same",
                                "initial": timestamp == 10,
                                "role": "broker",
                                "time": timestamp,
                                "stats": {
                                    "cpu_stats": {"cpu_usage": {"total_usage": cpu}},
                                    "memory_stats": {"usage": memory},
                                    "blkio_stats": {
                                        "io_service_bytes_recursive": [
                                            {"op": "Read", "value": read},
                                            {"op": "Write", "value": write},
                                        ]
                                    },
                                },
                            }
                        )
                        + "\n"
                    )
            row = metrics.summarize(path)["containers"]["member/same"]
            self.assertEqual(row["cpu_core_seconds"], 1)
            self.assertEqual(row["cpu_utilization_percent_one_core_100"]["average"], 50)
            self.assertEqual(row["sampled_mean_charged_bytes"], 150)
            self.assertEqual(row["block_io"]["read_bytes"], 128)
            self.assertEqual(row["block_io"]["write_bytes"], 256)
            self.assertIsNone(row["block_io"]["read_operations"])

    def test_container_restart_is_a_new_counter_incarnation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.gz"
            with gzip.open(path, "wt") as stream:
                for start, initial, cpu in [
                    ("old", True, 1000000000),
                    ("old", False, 3000000000),
                    ("new", False, 500000000),
                ]:
                    stream.write(
                        json.dumps(
                            {
                                "id": "same-container",
                                "start": start,
                                "initial": initial,
                                "role": "processor",
                                "stats": {
                                    "cpu_stats": {"cpu_usage": {"total_usage": cpu}},
                                    "memory_stats": {
                                        "usage": 2048,
                                        "stats": {"anon": 1024},
                                    },
                                },
                            }
                        )
                        + "\n"
                    )
            result = metrics.summarize(path)
            self.assertEqual(result["cpu_core_seconds"], 2.5)
            self.assertEqual(len(result["containers"]), 2)
            self.assertEqual(
                result["containers"]["same-container/new"][
                    "sampled_max_anonymous_bytes"
                ],
                1024,
            )

    def test_counter_regression_without_restart_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "stats.gz"
            with gzip.open(path, "wt") as stream:
                for cpu in [2, 1]:
                    stream.write(
                        json.dumps(
                            {
                                "id": "same",
                                "start": "unchanged",
                                "initial": True,
                                "role": "processor",
                                "stats": {
                                    "cpu_stats": {"cpu_usage": {"total_usage": cpu}}
                                },
                            }
                        )
                        + "\n"
                    )
            with self.assertRaises(RuntimeError):
                metrics.summarize(path)


class WitnessTests(unittest.TestCase):
    def test_interrupted_receipt_keeps_failure_and_rejects_changed_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            root = state / "cases" / "case"
            root.mkdir(parents=True)
            for name in ("failure.json", "experiment.json"):
                (root / name).write_text("{}")
            receipt = {
                "sha256": {
                    name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                    for name in ("failure.json", "experiment.json")
                },
                "proofs": {},
            }
            for side in ("source", "enriched"):
                output = root / side / "verifications" / "new"
                output.mkdir(parents=True)
                report = output / "verification.json"
                report.write_text(
                    json.dumps(
                        {
                            "strict_verification": True,
                            "acknowledged_loss": 0,
                            "corruption": 0,
                            "observed_acknowledged": 2,
                            "acknowledged": 2,
                            "run_id": "case",
                            "mirrored_offset_policy": side == "enriched",
                        }
                    )
                )
                receipt["proofs"][side] = {
                    "path": str(output.relative_to(state)),
                    "sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                }
            (root / "post-interruption-reconciliation.json").write_text(
                json.dumps(receipt)
            )
            with patch.object(lab, "STATE", state):
                self.assertTrue(control.reconciled_failure(root))
                self.assertTrue((root / "failure.json").exists())
                report.write_text('{"changed":true}')
                with self.assertRaises(RuntimeError):
                    control.reconciled_failure(root)

    def test_sql_ambiguity_does_not_become_an_ack(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            root = state / "cases" / "case"
            root.mkdir(parents=True)
            rows = [
                {"stage": "intent", "id": "a", "time": 1},
                {"stage": "ack", "id": "a", "time": 2, "seconds": 0.1, "version": 1},
                {"stage": "intent", "id": "b", "time": 3},
                {"stage": "ambiguous", "id": "b", "time": 4, "seconds": 0.2},
            ]
            with gzip.open(root / "postgres-writer.jsonl.gz", "wt") as stream:
                for row in rows:
                    stream.write(json.dumps(row) + "\n")
            with (
                patch.object(lab, "STATE", state),
                patch.object(lab, "sql", return_value='{"a":1,"b":2}'),
            ):
                result = probes.reconcile(root, [{"phase": "setup", "time": 0}])
                self.assertEqual(result["acknowledged"], 1)
                self.assertEqual(result["ambiguous_applied"], 1)
            for actual in ['{"b":2}', '{"a":9,"b":2}', '{"a":1,"alien":3}']:
                with (
                    patch.object(lab, "STATE", state),
                    patch.object(lab, "sql", return_value=actual),
                    self.assertRaises(RuntimeError),
                ):
                    probes.reconcile(root, [{"phase": "setup", "time": 0}])

    def test_empty_setup_seal_requires_unchanged_failure_and_zero_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source").mkdir()
            (root / "enriched").mkdir()
            (root / "experiment.json").write_text(
                json.dumps(
                    {
                        "owner": {"owner": "owned"},
                        "input": "orders-a",
                        "output": "orders-a.enriched",
                    }
                )
            )
            (root / "failure.json").write_text('{"passed":false}')
            (root / "phases.json").write_text("[]")

            def empty(_tool, *args):
                topic = args[args.index("--topic") + 1]
                return "\n".join(f"{topic}:{n}:0" for n in range(6))

            with (
                patch.object(lab, "owner", return_value={"owner": "owned"}),
                patch.object(lab, "cli", side_effect=empty),
                patch.object(probes, "reconcile", return_value={"loss": 0}),
            ):
                self.assertTrue(control.setup_only_failure(root, seal=True))
                self.assertTrue(control.setup_only_failure(root))
                (root / "failure.json").write_text('{"modified":true}')
                with self.assertRaises(RuntimeError):
                    control.setup_only_failure(root)
                (root / "source/producer.jsonl.gz").touch()
                with self.assertRaises(RuntimeError):
                    control.setup_only_failure(root, seal=True)


if __name__ == "__main__":
    unittest.main()
