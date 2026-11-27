"""Multipart publication without a client ACK must remain ambiguous in history."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import control


class MultipartHistory(unittest.TestCase):
    def test_discarded_response_does_not_become_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "fault-multipart-example"
            case.mkdir()
            for name, key, result in (
                ("churn", "regular", "ack"),
                ("multipart", "discarded", "ambiguous"),
            ):
                rows = [
                    {"stage": "attempt", "object": {"key": key}},
                    {"stage": result, "key": key},
                ]
                (case / (name + ".jsonl")).write_text(
                    "".join(json.dumps(r) + "\n" for r in rows)
                )
            with patch.object(control, "BASE", root):
                history = control.history()
            self.assertEqual(
                {r["object"]["key"]: r["acked"] for r in history},
                {"regular": True, "discarded": False},
            )


if __name__ == "__main__":
    unittest.main()
