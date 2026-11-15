"""Negative controls: corruption and unavailable data must never pass reconciliation."""

import io
import unittest
from unittest.mock import Mock
from botocore.exceptions import ClientError
import workload as w


class Integrity(unittest.TestCase):
    def setUp(self):
        self.store = w.Store.__new__(w.Store)
        self.store.s3 = Mock()
        self.value = w.record("key", b"correct", 3)

    def response(self, data=b"correct", version="3"):
        self.store.s3.get_object.return_value = {
            "Body": io.BytesIO(data),
            "ContentLength": len(data),
            "Metadata": {"sha256": self.value["sha256"], "version": version},
        }

    def test_correct_body(self):
        self.response()
        self.assertEqual(self.store.get(self.value), 7)

    def test_corrupt_same_length(self):
        self.response(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.store.get(self.value)

    def test_truncated_body(self):
        self.response(b"cor")
        with self.assertRaisesRegex(RuntimeError, "size/checksum"):
            self.store.get(self.value)

    def test_stale_version(self):
        self.response(version="2")
        with self.assertRaisesRegex(RuntimeError, "version"):
            self.store.get(self.value)

    def test_unavailable_is_not_absent(self):
        self.store.s3.head_object.side_effect = ClientError(
            {
                "ResponseMetadata": {"HTTPStatusCode": 503},
                "Error": {"Code": "Unavailable"},
            },
            "HeadObject",
        )
        with self.assertRaises(ClientError):
            self.store.missing("key")

    def test_repeated_token(self):
        self.store.s3.list_objects_v2.return_value = {
            "IsTruncated": True,
            "NextContinuationToken": "repeated",
        }
        with self.assertRaisesRegex(RuntimeError, "continuation token"):
            list(self.store.keys("prefix"))

    def test_missing_ack(self):
        self.store.expected = Mock(return_value=({"key": self.value}, {"key"}))
        self.store.keys = Mock(return_value=iter([]))
        with self.assertRaisesRegex(RuntimeError, "acknowledged"):
            self.store.verify()

    def test_partial_delete_failure(self):
        self.store.s3.get_paginator.return_value.paginate.return_value = []
        self.store.keys = Mock(return_value=iter(["key"]))
        self.store.s3.delete_objects.return_value = {
            "Errors": [{"Key": "key", "Code": "AccessDenied"}]
        }
        with self.assertRaisesRegex(RuntimeError, "partial batch deletion"):
            self.store.drop()
        self.store.s3.delete_bucket.assert_not_called()

    def test_recheck_historical_churn(self):
        value = w.record("churn/previous/1", b"correct", 3)
        self.store.expected = Mock(return_value=({value["key"]: value}, {value["key"]}))
        self.store.keys = Mock(
            side_effect=lambda prefix: iter(
                [value["key"]] if prefix == "churn/" else []
            )
        )
        self.response()
        self.assertEqual(self.store.verify()["objects_verified"], 1)

    def test_new_run_preserves_old_oracle_and_unrelated_files(self):
        from pathlib import Path
        import tempfile
        from run_state import archive_oracles

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "fault-history.json").write_text("old ACK evidence")
            (root / "unrelated.json").write_text("user data")
            (root / "active.log").write_text("open log")
            archive_oracles(root)
            self.assertFalse((root / "fault-history.json").exists())
            self.assertEqual(
                next((root / "previous-runs").glob("*/fault-history.json")).read_text(),
                "old ACK evidence",
            )
            self.assertEqual((root / "unrelated.json").read_text(), "user data")
            self.assertTrue((root / "active.log").exists())

    def test_unacknowledged_benchmark_attempt_is_optional(self):
        from unittest.mock import patch

        ack = w.record("bench/ack", b"ack")
        unknown = w.record("bench/unknown", b"maybe-committed")
        manifests = {"benchmark-manifest": [ack], "benchmark-attempts": [ack, unknown]}
        with patch.object(
            w, "load", side_effect=lambda name, default: manifests.get(name, default)
        ):
            expected, required = self.store.expected()
        self.assertEqual(set(expected), {ack["key"], unknown["key"]})
        self.assertEqual(required, {ack["key"]})
