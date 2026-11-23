"""Native recovery must account for table sync, not just queue lengths."""

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import control


class NativeRecovery(unittest.TestCase):
    def values(self):
        return (
            {
                "tableStats": [
                    {"tableName": "object", "merkleQueueLen": 0, "insertQueueLen": 0}
                ],
                "blockManagerStats": {"resyncErrors": 0, "resyncQueueLen": 17},
            },
            [
                {"name": "object" + suffix, "state": "idle", "consecutiveErrors": 0}
                for suffix in [" sync", " Merkle", " queue"]
            ],
        )

    def test_delayed_gc_queue_is_not_missing_live_data(self):
        stats, workers = self.values()
        self.assertTrue(control.metadata_idle(stats, workers))

    def test_busy_or_failed_sync_is_not_ready_even_with_empty_queues(self):
        stats, workers = self.values()
        for state in ["busy", {"throttled": {"durationSecs": 1}}, "done"]:
            changed = deepcopy(workers)
            changed[0]["state"] = state
            self.assertFalse(control.metadata_idle(stats, changed))
        changed = deepcopy(workers)
        changed[1]["state"] = "busy"
        self.assertFalse(control.metadata_idle(stats, changed))
        workers[0]["consecutiveErrors"] = 1
        self.assertFalse(control.metadata_idle(stats, workers))

    def test_missing_sync_worker_is_unknown_not_success(self):
        stats, _workers = self.values()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            control.metadata_idle(stats, [])


class BootstrapRecovery(unittest.TestCase):
    def test_partial_seed_is_not_replayed_or_marked_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"stage":"initializing"}')
            with (
                patch.object(control, "BASE", root),
                patch.object(control, "fixture_check"),
                patch.object(control, "repair") as repair,
                self.assertRaisesRegex(RuntimeError, "incomplete seed"),
            ):
                control.finish_bootstrap()
            repair.assert_not_called()
            self.assertIn("initializing", (root / "fixture.json").read_text())

    def test_failed_native_barrier_preserves_initializing_witness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"stage":"initializing"}')
            (root / "manifest.json").write_text("external ACK witness")
            (root / "seed.json").write_text("{}")
            with (
                patch.object(control, "BASE", root),
                patch.object(control, "fixture_check"),
                patch.object(control, "repair"),
                patch.object(
                    control, "verify", side_effect=RuntimeError("missing copy")
                ),
                self.assertRaisesRegex(RuntimeError, "missing copy"),
            ):
                control.finish_bootstrap()
            self.assertIn("initializing", (root / "fixture.json").read_text())
            self.assertEqual(
                (root / "manifest.json").read_text(), "external ACK witness"
            )
