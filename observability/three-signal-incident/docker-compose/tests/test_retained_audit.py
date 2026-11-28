"""A crashed, unfenced publication cannot disappear from a cleanup audit."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import experiments


class RetainedAudit(unittest.TestCase):
    def test_unfenced_journal_blocks_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results/cases/partial"
            (case / "greptime/metrics").mkdir(parents=True)
            (case / "case.json").write_text(json.dumps({"kind": "healthy"}))
            (case / "greptime/metrics/requests.jsonl").write_text("{}\n")
            with patch.object(experiments, "ROOT", root), patch.object(
                experiments, "running_actors", return_value=[]
            ), patch.object(experiments, "assert_source"), patch.object(
                experiments, "is_retired", return_value=False
            ), patch.object(
                experiments, "select_backend"
            ) as select:
                with self.assertRaisesRegex(RuntimeError, "Unfenced publication"):
                    experiments.audit_retained()
                select.assert_not_called()
            statuses = list((root / "results/audits").glob("*/status.json"))
            self.assertEqual(len(statuses), 1)
            self.assertFalse(
                json.loads(statuses[0].read_text())["all_ack_values_present"]
            )


if __name__ == "__main__":
    unittest.main()
