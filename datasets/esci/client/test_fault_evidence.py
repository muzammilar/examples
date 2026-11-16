"""A rerun must preserve the ACK oracle and fail before touching Docker."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class FaultEvidence(unittest.TestCase):
    def test_existing_evidence_rejects_every_engine_before_docker(self):
        script = Path(__file__).with_name("fault.py")
        with tempfile.TemporaryDirectory() as tmp:
            for engine in ("typesense", "vespa", "pgvector", "lancedb"):
                for name in (
                    "churn.jsonl",
                    "churn.json",
                    "fault.json",
                    "reconciliation.json",
                ):
                    with self.subTest(engine=engine, evidence=name):
                        folder = Path(tmp) / engine / name.replace(".", "-")
                        folder.mkdir(parents=True)
                        evidence = folder / name
                        original = b"original-witness\n"
                        evidence.write_bytes(original)
                        result = subprocess.run(
                            [sys.executable, str(script)],
                            env=dict(
                                os.environ,
                                ENGINE=engine,
                                MODE="cluster",
                                RESULTS_DIR=str(folder),
                                PATH=str(folder / "no-executables"),
                            ),
                            text=True,
                            capture_output=True,
                            timeout=10,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("prior fault evidence exists", result.stderr)
                        self.assertEqual(evidence.read_bytes(), original)
