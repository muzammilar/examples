import gzip
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import control
from view_proofs import audit_views


class HostControls(unittest.TestCase):
    def test_unknown_inspection_is_not_fencing(self):
        with patch("control.call", side_effect=RuntimeError("transport unavailable")):
            with self.assertRaises(RuntimeError):
                control.fenced("old-id")

    def test_signal_and_ownership_are_required(self):
        for running, code, oom in [
            (True, 0, False),
            (False, 0, False),
            (False, 137, True),
        ]:
            with patch(
                "control.inspect_id",
                return_value={
                    "State": {"Running": running, "ExitCode": code, "OOMKilled": oom}
                },
            ):
                with self.assertRaises(RuntimeError):
                    control.fenced("id")

    def test_success_and_error_latencies_are_separate(self):
        result = control.window(
            [
                {"time": 1, "stage": "ack", "seconds": 1, "rows": [{}]},
                {"time": 2, "stage": "ambiguous", "seconds": 0.001, "rows": [{}]},
            ],
            0,
            3,
        )
        self.assertEqual(result["ack"]["p50_ms"], 1000)
        self.assertEqual(result["ambiguous"]["p50_ms"], 1)

    def test_mount_order_is_not_identity(self):
        a = {"Type": "volume", "Destination": "/data", "Name": "owned"}
        b = {"Type": "bind", "Destination": "/corpus", "Source": "input"}
        self.assertEqual(
            control.mount_records({"Mounts": [a, b]}),
            control.mount_records({"Mounts": [b, a]}),
        )

    def test_proof_requires_all_views_and_same_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            views = {}
            for node in ["writer", "reader1"]:
                body = (
                    json.dumps({"id": "0", "version": 1, "vector_sha256": "0" * 64})
                    + "\n"
                )
                path = root / (node + ".gz")
                with gzip.open(path, "wt") as stream:
                    stream.write(body)
                views[node] = {
                    "raw": path.name,
                    "raw_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "rows": 1,
                    "vector_coordinates": 384,
                }
            report = {"scope": "views-of-one-shared-catalog", "views": views}
            self.assertTrue(
                audit_views(report, root, ["writer", "reader1"], 1)[
                    "all_required_views_identical"
                ]
            )
            with self.assertRaises(ValueError):
                audit_views(report, root, ["writer", "reader1", "reader2"], 1)
            witness = root / "cases" / "previous" / "writer.jsonl"
            witness.parent.mkdir(parents=True)
            witness.write_text(
                json.dumps({"stage": "ack", "rows": [{"id": "0", "version": 2}]}) + "\n"
            )
            with self.assertRaises(ValueError):
                audit_views(report, root, ["writer", "reader1"], 1)
            witness.write_text(
                json.dumps({"stage": "ack", "rows": [{"id": "0", "version": 1}]}) + "\n"
            )
            acknowledged = audit_views(report, root, ["writer", "reader1"], 1)
            self.assertEqual(
                acknowledged["views"]["writer"]["visible_latest_nonack_versions"], 0
            )

            path = root / "reader1.gz"
            with gzip.open(path, "wt") as stream:
                stream.write(body.replace('"version": 1', '"version": 2'))
            views["reader1"]["raw_sha256"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            with self.assertRaises(ValueError):
                audit_views(report, root, ["writer", "reader1"], 1)

    def test_stale_running_sdk_adapter_is_rejected(self):
        with patch("control.call", return_value="0" * 64 + " /app/server.py"):
            with self.assertRaises(RuntimeError):
                control.assert_adapter_sources({"writer": {"Id": "owned"}})

    def test_reused_cached_native_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "command-state.json").write_text('{"stale":true}')
            with (
                patch("control.RESULTS", root),
                patch("control.call", return_value="no output"),
            ):
                with self.assertRaises(RuntimeError):
                    control.tools("state")
            self.assertFalse((root / "command-state.json").exists())
            self.assertEqual(len(list((root / "command-history").iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
