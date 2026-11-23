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
                    k: []
                    for k in [
                        "containers",
                        "volumes",
                        "networks",
                        "build_tag",
                        "witnessed_volumes",
                    ]
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
            (root / "block-placement.json").write_text("not a SeaweedFS artifact")
            (root / "reconciliation.json").write_text('{"objects_verified":100}')
            (root / "fault-failed").mkdir()
            (root / "fault-failed/status.json").write_text('{"passed":false}')
            proof = {
                "fixture_id": "old",
                "project": control.PROJECT,
                "finished": True,
                "remaining": {
                    k: []
                    for k in [
                        "containers",
                        "volumes",
                        "networks",
                        "build_tag",
                        "witnessed_volumes",
                    ]
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
                (root / "block-placement.json").read_text(), "not a SeaweedFS artifact"
            )
            self.assertEqual(
                (archived[0] / "reconciliation.json").read_text(),
                '{"objects_verified":100}',
            )


class CleanupProof(unittest.TestCase):
    def test_unlabelled_witnessed_volume_prevents_success_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text(
                json.dumps(
                    {
                        "id": "old",
                        "project": control.PROJECT,
                        "volumes": {"exact-owned-anonymous": "birth"},
                    }
                )
            )

            def response(args):
                return (
                    "exact-owned-anonymous\nunrelated-anonymous\n"
                    if args == ["docker", "volume", "ls", "-q"]
                    else ""
                )

            with (
                patch.object(control, "BASE", root),
                patch.object(control, "compose") as compose,
                patch.object(control, "run", side_effect=response),
                self.assertRaisesRegex(RuntimeError, "teardown left owned"),
            ):
                control.teardown()
            compose.assert_called_once_with(
                "--profile", "tools", "down", "--volumes", "--remove-orphans"
            )
            self.assertFalse((root / "teardown.json").exists())

    def test_unrelated_anonymous_volume_does_not_block_scoped_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fixture.json").write_text(
                json.dumps(
                    {
                        "id": "old",
                        "project": control.PROJECT,
                        "volumes": {"exact-owned-anonymous": "birth"},
                    }
                )
            )

            def response(args):
                return (
                    "unrelated-anonymous\n"
                    if args == ["docker", "volume", "ls", "-q"]
                    else ""
                )

            with (
                patch.object(control, "BASE", root),
                patch.object(control, "compose"),
                patch.object(control, "run", side_effect=response) as run,
            ):
                control.teardown()
            proof = json.loads((root / "teardown.json").read_text())
            self.assertTrue(proof["finished"])
            self.assertFalse(any(proof["remaining"].values()))
            self.assertFalse(
                any(
                    call.args[0][:3] == ["docker", "volume", "rm"]
                    for call in run.call_args_list
                )
            )
