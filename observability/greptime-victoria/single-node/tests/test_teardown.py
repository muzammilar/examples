"""A corrupt or stale local receipt must never authorize destruction/retirement."""

import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
lifecycle = importlib.import_module("lifecycle")


class TeardownGuards(unittest.TestCase):
    def test_new_witness_invalidates_destructive_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "results").mkdir()
            (root / "results/verified-down.json").write_text(
                json.dumps({"verified": True, "witnesses": {"old": "hash"}})
            )
            with patch.object(lifecycle, "ROOT", root), patch.object(
                lifecycle, "witness_hashes", return_value={"new": "hash"}
            ), patch.object(lifecycle, "command") as mutation:
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    lifecycle.destroy()
                mutation.assert_not_called()

    def test_unrelated_completed_receipt_cannot_retire_an_active_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results/cases/new-case/case.json"
            case.parent.mkdir(parents=True)
            case.write_text("{}")
            (root / "results/retired-cases.json").write_text(
                json.dumps({"new-case": "results/prior-cleanup.json"})
            )
            (root / "results/prior-cleanup.json").write_text(
                json.dumps(
                    {
                        "owned_resources_absent": True,
                        "cases": ["old-case"],
                        "witnesses": {},
                    }
                )
            )
            with patch.object(lifecycle, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "cleanup evidence"):
                    lifecycle.is_retired(case)

    def test_changed_retired_data_is_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results/cases/old-case/case.json"
            case.parent.mkdir(parents=True)
            case.write_text("changed")
            (root / "results/retired-cases.json").write_text(
                json.dumps({"old-case": "results/prior-cleanup.json"})
            )
            (root / "results/prior-cleanup.json").write_text(
                json.dumps(
                    {
                        "owned_resources_absent": True,
                        "cases": ["old-case"],
                        "witnesses": {
                            "results/cases/old-case/case.json": "bad-checksum"
                        },
                    }
                )
            )
            with patch.object(lifecycle, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "witness changed"):
                    lifecycle.is_retired(case)


if __name__ == "__main__":
    unittest.main()
