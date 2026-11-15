"""Negative recovery gates; live tests exercise real kernels and storage."""

import unittest
from unittest.mock import patch

import lab


class FioCompletionTests(unittest.TestCase):
    def test_rejects_short_io_even_without_fio_error(self):
        with self.assertRaisesRegex(RuntimeError, "complete expected byte range"):
            lab.checked_fio({"jobs": [{"error": 0, "write": {"io_bytes": 1048575}}]}, 1)

    def test_rejects_io_error_even_with_complete_byte_count(self):
        with self.assertRaisesRegex(RuntimeError, "fio failed"):
            lab.checked_fio({"jobs": [{"error": 5, "write": {"io_bytes": 1048576}}]}, 1)

    def test_requires_a_job_result(self):
        with self.assertRaisesRegex(RuntimeError, "fio failed"):
            lab.checked_fio({"jobs": []}, 1)


class PromotionGateTests(unittest.TestCase):
    def run_rejected_promotion(self, reason):
        def ssh(node, command, **kwargs):
            if "receive_resume_token" in command:
                return "-" if reason == "no-resume-token" else "resume-token"
            if command == "zpool export zfslab" and reason == "export-failed":
                raise RuntimeError("injected export failure")
            if "echo imported" in command:
                return "imported" if reason == "still-imported" else "fenced"
            return ""

        def info(node, filename):
            return {
                "bytes": 1,
                "sha256": "bad"
                if node == "target" and reason == "bad-hash"
                else "good",
            }

        with (
            patch.object(lab, "reset"),
            patch.object(lab, "fio_file"),
            patch.object(lab, "file_info", side_effect=info),
            patch.object(lab, "replicate", return_value={}),
            patch.object(lab, "ssh", side_effect=ssh) as remote,
            patch.object(lab, "save") as save,
        ):
            with self.assertRaises(RuntimeError):
                lab.replication_test()
            commands = [call.args[1] for call in remote.call_args_list]
            self.assertFalse(any("readonly=off" in command for command in commands))
            save.assert_not_called()
            if reason in ("no-resume-token", "bad-hash"):
                self.assertNotIn("zpool export zfslab", commands)

    def test_missing_resume_token_cannot_promote(self):
        self.run_rejected_promotion("no-resume-token")

    def test_corrupt_received_copy_cannot_promote(self):
        self.run_rejected_promotion("bad-hash")

    def test_failed_source_export_cannot_promote(self):
        self.run_rejected_promotion("export-failed")

    def test_source_still_imported_cannot_promote(self):
        self.run_rejected_promotion("still-imported")


class PoolCreationTests(unittest.TestCase):
    def test_unrelated_failure_is_not_retried(self):
        with patch.object(
            lab, "ssh", side_effect=["", RuntimeError("read-only device")]
        ) as remote:
            with self.assertRaisesRegex(RuntimeError, "read-only"):
                lab.reset("source", "mirror")
            self.assertEqual(remote.call_count, 2)

    def test_existing_pool_stops_creation_retry(self):
        with patch.object(
            lab,
            "ssh",
            side_effect=[
                "",
                RuntimeError("one or more devices is currently unavailable"),
                "exists",
            ],
        ) as remote:
            with self.assertRaisesRegex(RuntimeError, "pool exists"):
                lab.reset("source", "mirror")
            self.assertEqual(remote.call_count, 3)

    def test_only_settled_partitions_are_used_for_single_retry(self):
        with patch.object(
            lab,
            "ssh",
            side_effect=[
                "",
                RuntimeError("one or more devices is currently unavailable"),
                "absent",
                "",
                "",
            ],
        ) as remote:
            lab.reset("source", "raidz2")
            commands = [call.args[1] for call in remote.call_args_list]
            self.assertIn("udevadm settle --timeout=60", commands[3])
            self.assertIn("test -b /dev/disk/by-id/virtio-zfslab-d3-part1", commands[3])
            self.assertIn("virtio-zfslab-d3-part1", commands[4])
            self.assertEqual(remote.call_count, 5)


if __name__ == "__main__":
    unittest.main()
