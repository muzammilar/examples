"""A fresh run may retire an old witness only after matching successful teardown."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control


class RetireFixture(unittest.TestCase):
    def test_teardown_for_another_fixture_cannot_retire_witness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"id":"old"}')
            (root / "manifest.json").write_text("old ACK witness")
            proof = {
                "fixture_id": "other",
                "project": control.PROJECT,
                "finished": True,
                "remaining": {
                    k: [] for k in ["containers", "volumes", "networks", "build_tag"]
                },
            }
            (root / "teardown.json").write_text(json.dumps(proof))
            with (
                patch.object(control, "BASE", root),
                self.assertRaisesRegex(RuntimeError, "invalid teardown"),
            ):
                control.retire_removed_fixture()
            self.assertEqual((root / "manifest.json").read_text(), "old ACK witness")

    def test_verified_teardown_archives_failed_history_and_ignores_unrelated_files(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text('{"id":"old"}')
            (root / "manifest.json").write_text("old ACK witness")
            (root / "unrelated.json").write_text("user notes")
            (root / "reconciliation.json").write_text('{"objects_verified":100}')
            (root / "fault-failed").mkdir()
            (root / "fault-failed/status.json").write_text('{"passed":false}')
            proof = {
                "fixture_id": "old",
                "project": control.PROJECT,
                "finished": True,
                "remaining": {
                    k: [] for k in ["containers", "volumes", "networks", "build_tag"]
                },
            }
            (root / "teardown.json").write_text(json.dumps(proof))
            with patch.object(control, "BASE", root):
                control.retire_removed_fixture()
            archived = list((root / "previous-runs").iterdir())
            self.assertEqual(len(archived), 1)
            self.assertEqual(
                (archived[0] / "manifest.json").read_text(), "old ACK witness"
            )
            self.assertEqual(
                (archived[0] / "fault-failed/status.json").read_text(),
                '{"passed":false}',
            )
            self.assertEqual((root / "unrelated.json").read_text(), "user notes")
            self.assertEqual(
                (archived[0] / "reconciliation.json").read_text(),
                '{"objects_verified":100}',
            )
