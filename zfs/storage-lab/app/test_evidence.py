"""Negative controls for external histories and native recovery evidence."""

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

import evidence
import lab
import observe


class EvidenceTests(unittest.TestCase):
    def test_prior_history_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed.json"
            evidence.save(path, {"passed": False})
            with self.assertRaises(FileExistsError):
                evidence.save(path, {"passed": True})
            self.assertFalse(json.loads(path.read_text())["passed"])

    def test_full_body_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "body"
            path.write_bytes(bytes(range(256)) * 100)
            expected = observe.inspect_file(path)
            self.assertEqual(
                expected["sha256"], hashlib.sha256(path.read_bytes()).hexdigest()
            )
            path.write_bytes(b"x" + path.read_bytes()[1:])
            self.assertNotEqual(observe.inspect_file(path), expected)

    def test_native_zero_exit_does_not_hide_corruption_or_truncation(self):
        good = b"hdrtype = 1\nhdrtype = 1\nTotal DRR_BEGIN records = 2\nTotal DRR_END records = 2\nTotal stream length = 1024\n"
        self.assertTrue(
            evidence.stream_complete(
                subprocess.CompletedProcess([], 0, good, b""), 1024
            )
        )
        for stdout, stderr in (
            (good, b"invalid checksum"),
            (good + b"Expected checksum differs from checksum in stream.\n", b""),
            (good.replace(b"END records = 2", b"END records = 1"), b""),
            (good, b""),
            (b"", b""),
        ):
            size = 1025 if stdout == good and not stderr else 1024
            self.assertFalse(
                evidence.stream_complete(
                    subprocess.CompletedProcess([], 0, stdout, stderr), size
                )
            )

    def test_send_stream_must_cover_each_native_dataset_snapshot(self):
        text = b"hdrtype = 1\ntoname = zfslab@evidence\nTotal DRR_BEGIN records = 1\nTotal DRR_END records = 1\nTotal stream length = 1024\n"
        result = subprocess.CompletedProcess([], 0, text, b"")
        self.assertTrue(evidence.stream_complete(result, 1024, ["zfslab@evidence"]))
        self.assertFalse(
            evidence.stream_complete(
                result, 1024, ["zfslab@evidence", "zfslab/data@evidence"]
            )
        )

    def test_zero_exit_send_warning_still_preserves_native_storage(self):
        with tempfile.TemporaryDirectory() as directory:

            def ssh(node, command):
                if command == "zpool list -H -o name":
                    return "zfslab"
                if command.startswith("zfs list -H -r -t snapshot"):
                    return "zfslab@" + snapshot[0]
                if command.startswith("zfs snapshot -r"):
                    snapshot.append(command.split("@")[-1])
                return ""

            snapshot = []
            with patch.object(
                evidence.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 0, b"", b"cannot send dataset"
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "archive failed"):
                    evidence.archive_pool(
                        "source", Path(directory), lambda *args: [], ssh
                    )
            self.assertEqual(
                len(list(Path(directory).glob("pool-archives/*/failure.json"))), 1
            )

    def test_recursive_package_requires_its_extra_conclusion_end(self):
        output = b"hdrtype = 2\nhdrtype = 1\nhdrtype = 1\nTotal DRR_BEGIN records = 3\nTotal DRR_END records = 4\nTotal stream length = 1024\n"
        self.assertTrue(
            evidence.stream_complete(
                subprocess.CompletedProcess([], 0, output, b""), 1024
            )
        )
        for count in (3, 5):
            bad = output.replace(b"END records = 4", f"END records = {count}".encode())
            self.assertFalse(
                evidence.stream_complete(
                    subprocess.CompletedProcess([], 0, bad, b""), 1024
                )
            )
        bad = output.replace(b"hdrtype = 2", b"hdrtype = 1")
        self.assertFalse(
            evidence.stream_complete(subprocess.CompletedProcess([], 0, bad, b""), 1024)
        )

    def test_archive_failure_prevents_destructive_reset(self):
        with (
            patch.object(
                lab.evidence, "archive_pool", side_effect=RuntimeError("archive failed")
            ),
            patch.object(lab, "ssh") as remote,
        ):
            with self.assertRaisesRegex(RuntimeError, "archive failed"):
                lab.reset("source", "mirror")
            remote.assert_not_called()

    def test_reads_must_finish_inside_actual_native_repair_interval(self):
        read = {
            "kind": "read",
            "result": "ok",
            "started_monotonic_ns": 10,
            "ended_monotonic_ns": 20,
        }
        self.assertEqual(evidence.reads_within([read], 5, 25), [read])
        for start, end in ((11, 25), (5, 19), (21, 25)):
            self.assertEqual(evidence.reads_within([read], start, end), [])
        self.assertEqual(
            evidence.reads_within([dict(read, result="mismatch")], 5, 25), []
        )

    def test_overlap_waits_for_inflight_journal_delivery(self):
        reader = SimpleNamespace(records=[], error=None, process=Mock())
        reader.process.poll.return_value = None
        read = {
            "kind": "read",
            "result": "ok",
            "started_monotonic_ns": 10,
            "ended_monotonic_ns": 20,
        }
        with patch.object(
            evidence.time, "sleep", side_effect=lambda _: reader.records.append(read)
        ):
            evidence.Reader.wait_overlap(reader, 5, 25)
        reader.records = [dict(read, started_monotonic_ns=30, ended_monotonic_ns=40)]
        with self.assertRaisesRegex(RuntimeError, "no complete independent read"):
            evidence.Reader.wait_overlap(reader, 5, 25)


if __name__ == "__main__":
    unittest.main()
