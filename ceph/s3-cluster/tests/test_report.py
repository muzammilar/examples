"""A report must reject stale proof even when object totals could still match."""

import os
from pathlib import Path
import tempfile
import unittest

from report import freshness


class ProofFreshness(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in [
            "manifest",
            "cases-manifest",
            "benchmark-manifest",
            "benchmark-attempts",
            "fault-history",
        ]:
            self.touch(name, 100)
        self.touch("native-audit", 200)
        self.touch("reconciliation", 300)
        self.touch("native-state", 400)

    def touch(self, name, timestamp):
        path = self.root / (name + ".json")
        path.write_text("{}")
        os.utime(path, ns=(timestamp, timestamp))

    def test_completed_proof_after_every_witness_is_accepted(self):
        freshness(self.root)

    def test_new_benchmark_intent_or_ack_invalidates_old_proof(self):
        for name in ["benchmark-attempts", "benchmark-manifest"]:
            with self.subTest(name=name):
                self.touch(name, 500)
                with self.assertRaisesRegex(RuntimeError, "proof predates witness"):
                    freshness(self.root)
                self.touch(name, 100)

    def test_native_observation_before_body_readback_is_rejected(self):
        self.touch("native-state", 250)
        with self.assertRaisesRegex(RuntimeError, "native state predates body"):
            freshness(self.root)
