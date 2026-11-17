"""Negative controls for preserved history and destructive-operation fences."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import control
from version_history import EventTail, History, digest, empty, records
from report import validate_proof
from metrics import summarize


ROW = {"id": "0", "version": 1, "tenant": 1, "stock": 1, "price_cents": 101}


def event(stage, **changes):
    return {"stage": stage, "time": 1.0, "rows": [ROW.copy()]} | changes


class HistoryControls(unittest.TestCase):
    def test_ack_requires_exact_intent_and_unique_outcome(self):
        for invalid in [event("ack"), event("rejected"), event("ambiguous")]:
            with self.assertRaises(RuntimeError):
                History(empty()).append(invalid)
        history = History(empty())
        history.append(event("attempt"))
        with self.assertRaises(RuntimeError):
            history.append(event("ack", rows=[ROW | {"stock": 9}]))
        history.append(event("ack"))
        with self.assertRaises(RuntimeError):
            history.append(event("ack"))
        self.assertEqual(history.ack, {"0": 1})

    def test_cross_case_history_follows_versions_instead_of_fault_names(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(control, "BASE", Path(directory)),
        ):
            root = Path(directory)
            control.save(root / "fixture.json", {"id": "fixture"})
            for kind, version in [("leader", 1), ("follower", 2), ("disk", 3)]:
                case = root / ("fault-" + kind)
                case.mkdir()
                control.save(
                    case / "case.json",
                    {
                        "start_version": version,
                        "fixture_id": "fixture",
                        "mode": control.MODE,
                    },
                )
                row = ROW | {"version": version}
                (case / "writer.jsonl").write_text(
                    "".join(
                        json.dumps(event(stage, rows=[row])) + "\n"
                        for stage in ["attempt", "ack"]
                    )
                )
            history = control.history()
            self.assertEqual(history.ack, {"0": 3})
            self.assertEqual(history.acknowledgements, 3)
            self.assertEqual(history.operations, 3)

    def test_global_versions_cannot_repeat_for_different_ids(self):
        history = History(empty())
        history.append(event("attempt"))
        with self.assertRaises(RuntimeError):
            history.append(event("attempt", rows=[ROW | {"id": "1"}]))

    def test_pending_intents_and_ambiguous_outcomes_are_retained(self):
        history = History(empty())
        history.append(event("attempt"))
        history.append(event("ambiguous"))
        restored = History(history.value())
        self.assertEqual(restored.attempted, {"0": {1}})
        self.assertEqual(restored.ack, {})
        self.assertEqual(restored.last_version, 1)

    def test_incomplete_line_is_never_an_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "writer.jsonl"
            data = (json.dumps(event("ack")) + "\n").encode()
            path.write_bytes(data[:-1])
            tail = EventTail(path)
            try:
                self.assertEqual(tail.read(), [])
                self.assertEqual(records(path, live=True), [])
                with self.assertRaises(RuntimeError):
                    records(path)
                with path.open("ab") as stream:
                    stream.write(b"\n")
                self.assertEqual(tail.read(), [event("ack")])
                self.assertEqual(tail.read(), [])
            finally:
                tail.close()

    def test_historical_ack_needs_attempt_and_timestamp(self):
        for bad in [
            empty() | {"ack_versions": {"0": 1}},
            empty() | {"ack_versions": {"0": 1}, "attempted_versions": {"0": [1]}},
        ]:
            with self.assertRaises(RuntimeError):
                History(bad)


class StorageFences(unittest.TestCase):
    def test_concurrent_controllers_cannot_acquire_the_same_project(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(control, "ROOT", Path(directory)),
        ):
            with control.controller_lock():
                with self.assertRaises(RuntimeError):
                    with control.controller_lock():
                        self.fail("concurrent controller acquired the project")
            with control.controller_lock():
                pass

    def test_missing_disk_does_not_authorize_compose_to_create_an_unplanned_replacement(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory)
            control.save(
                case / "replacement.json",
                {"old_volume": {"name": "owned", "created": "old"}},
            )
            with (
                patch.object(
                    control,
                    "owned_volume",
                    side_effect=RuntimeError("inspection failed"),
                ),
                patch.object(control, "compose") as compose,
            ):
                with self.assertRaises(RuntimeError):
                    control.restore_target(case, "ts2", "disk", {"ts2": {}})
                compose.assert_not_called()

    def test_replacement_requires_saved_uuid_owner_name_and_new_birth(self):
        plan = {
            "old_volume": {"name": "owned_ts2", "created": "old"},
            "target": "ts2",
            "replacement_id": "unique",
        }
        value = {
            "Name": "owned_ts2",
            "CreatedAt": "new",
            "Labels": {
                "com.docker.compose.project": control.PROJECT,
                "com.docker.compose.volume": "ts2",
                "examples.replacement": "unique",
            },
        }
        self.assertEqual(
            control.replacement_volume(plan, value),
            {"name": "owned_ts2", "created": "new"},
        )
        invalid = [value | {"Name": "foreign"}, value | {"CreatedAt": "old"}]
        for label in value["Labels"]:
            wrong = copy.deepcopy(value)
            wrong["Labels"][label] = "foreign"
            invalid.append(wrong)
        for wrong in invalid:
            with self.assertRaises(RuntimeError):
                control.replacement_volume(plan, wrong)

    def test_missing_teardown_inventory_cannot_authorize_archival(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(control, "BASE", Path(directory)),
        ):
            root = Path(directory)
            control.save(root / "fixture.json", {"id": "fixture"})
            control.save(
                root / "teardown.json",
                {
                    "project": control.PROJECT,
                    "mode": control.MODE,
                    "fixture_id": "fixture",
                    "finished": True,
                    "remaining": {},
                },
            )
            with self.assertRaises(RuntimeError):
                control.retire_removed_fixture()
            self.assertTrue((root / "fixture.json").exists())

    def test_valid_teardown_archives_owned_history_and_preserves_user_files(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(control, "BASE", Path(directory)),
        ):
            root = Path(directory)
            control.save(root / "fixture.json", {"id": "fixture"})
            control.save(
                root / "teardown.json",
                {
                    "project": control.PROJECT,
                    "mode": control.MODE,
                    "fixture_id": "fixture",
                    "finished": True,
                    "remaining": {
                        key: []
                        for key in ["containers", "volumes", "networks", "build_tags"]
                    },
                },
            )
            (root / "my-notes.txt").write_text("keep")
            (root / "tenant-benchmark.json").write_text("preserve all tenant samples")
            control.retire_removed_fixture()
            self.assertEqual((root / "my-notes.txt").read_text(), "keep")
            archives = list((root / "previous-runs").iterdir())
            self.assertEqual(len(archives), 1)
            self.assertEqual(
                (archives[0] / "tenant-benchmark.json").read_text(),
                "preserve all tenant samples",
            )
            self.assertEqual(
                json.loads((archives[0] / "fixture.json").read_text()),
                {"id": "fixture"},
            )


class MetricsControls(unittest.TestCase):
    def test_partial_request_is_not_counted_twice_or_as_fully_successful(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = {
                "phases.jsonl": [
                    {"phase": "baseline", "time": 1},
                    {"phase": "verified", "time": 10},
                ],
                "writer.jsonl": [
                    event("attempt", rows=[ROW, ROW | {"id": "1"}]),
                    event("attempt", time=2.5, rows=[ROW | {"version": 2}]),
                    event("ack", time=2, seconds=0.001, request_id="mixed"),
                    event(
                        "ambiguous",
                        time=2.1,
                        seconds=0.001,
                        request_id="mixed",
                        rows=[ROW | {"id": "1"}],
                    ),
                    event(
                        "ack",
                        time=3,
                        seconds=0.2,
                        request_id="good",
                        rows=[ROW | {"version": 2}],
                    ),
                ],
                "reader.jsonl": [
                    {
                        "stage": "search",
                        "time": 2,
                        "seconds": 0.1,
                        "attempts": 2,
                        "error": "first peer down",
                    },
                    {
                        "stage": "search_error",
                        "time": 3,
                        "seconds": 0.001,
                        "attempts": 3,
                    },
                ],
            }
            for name, values in files.items():
                (root / name).write_text(
                    "".join(json.dumps(value) + "\n" for value in values)
                )
            result = summarize(root)
            phase = result["phases"]["baseline"]
            self.assertEqual(result["write_request_latency_samples"], 2)
            self.assertEqual(phase["successful_write_requests"]["p50_ms"], 200)
            self.assertEqual(phase["unresolved_or_rejected_write_requests"]["count"], 1)
            self.assertEqual(phase["additional_read_attempts"], 3)
            self.assertEqual(phase["successful_operations_with_failed_attempts"], 1)
            self.assertEqual(
                result["longest_successful_completion_gap_seconds"]["search"], 1
            )

    def test_shard_fanout_and_intentional_writer_stop_are_not_failover_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            control.save(root / "case.json", {"mode": "sharded"})
            files = {
                "phases.jsonl": [
                    {"phase": "baseline", "time": 1},
                    {"phase": "reconcile", "time": 3.1},
                    {"phase": "verified", "time": 50},
                ],
                "writer.jsonl": [event("ack", time=3, seconds=0.1, request_id="good")],
                "reader.jsonl": [
                    {"stage": "search", "time": 2, "seconds": 0.1, "attempts": 3},
                    {"stage": "search", "time": 4, "seconds": 0.1, "attempts": 4},
                ],
            }
            for name, values in files.items():
                (root / name).write_text(
                    "".join(json.dumps(value) + "\n" for value in values)
                )
            result = summarize(root)
            self.assertEqual(
                sum(p["additional_read_attempts"] for p in result["phases"].values()), 1
            )
            self.assertAlmostEqual(
                result["longest_successful_completion_gap_seconds"]["ack"], 2
            )

    def test_failed_open_phase_keeps_its_last_durable_observations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "phases.jsonl").write_text(
                json.dumps({"phase": "repair", "time": 1}) + "\n"
            )
            (root / "writer.jsonl").write_text(
                json.dumps(event("ack", time=2, seconds=0.1, request_id="request"))
                + "\n"
            )
            result = summarize(root)
            self.assertEqual(result["phases"]["repair"]["ack"]["rows"], 1)


class ProofControls(unittest.TestCase):
    def proof(self):
        fixture = {
            "id": "fixture",
            "mode": "cluster",
            "corpus_sha256": {"input": "hash"},
        }
        witness = History(empty())
        copies = {
            f"http://ts{i}:8108": {
                "shard": 0,
                "rows": 2,
                "vector_coordinates": 768,
                "missing_ids": 0,
                "stale_versions": 0,
                "wrong_payloads": 0,
                "misplaced_ids": 0,
            }
            for i in range(1, 4)
        }
        proof = {
            "fixture_id": "fixture",
            "mode": "cluster",
            "fixture_sha256": fixture["corpus_sha256"],
            "acknowledgement_floor_sha256": digest(witness.value()),
            "copies": copies,
            "native_before": {
                peer: {
                    "shard": 0,
                    "state": "LEADER" if peer == "http://ts1:8108" else "FOLLOWER",
                    "committed_index": 100,
                    "queued_writes": 0,
                }
                for peer in copies
            },
            "native_after": {
                peer: {
                    "shard": 0,
                    "state": "LEADER" if peer == "http://ts1:8108" else "FOLLOWER",
                    "committed_index": 100,
                    "queued_writes": 0,
                }
                for peer in copies
            },
            "committed_watermarks": {"0": 100},
            "finished_at": 1,
            "rows_checked": 6,
            "vector_coordinates_checked": 2304,
        }
        return fixture, witness, proof

    def test_current_exact_topology_and_counts_are_required(self):
        fixture, witness, proof = self.proof()
        validate_proof(fixture, witness, proof, {0: 2})
        invalid = []
        for key, value in [
            ("fixture_id", "other"),
            ("acknowledgement_floor_sha256", "stale"),
            ("rows_checked", 5),
            ("finished_at", 0),
        ]:
            invalid.append(proof | {key: value})
        missing = copy.deepcopy(proof)
        del missing["copies"]["http://ts2:8108"]
        invalid.append(missing)
        wrong = copy.deepcopy(proof)
        wrong["copies"]["http://ts2:8108"]["rows"] = 1
        invalid.append(wrong)
        for bad in invalid:
            with self.assertRaises(RuntimeError):
                validate_proof(fixture, witness, bad, {0: 2})

    def test_new_ack_invalidates_old_proof(self):
        fixture, witness, proof = self.proof()
        witness.append(event("attempt"))
        witness.append(event("ack"))
        with self.assertRaises(RuntimeError):
            validate_proof(fixture, witness, proof, {0: 2})


if __name__ == "__main__":
    unittest.main()
