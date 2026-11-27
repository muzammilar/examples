"""A retryable status-propagation 404 must not hide other API failures."""

import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lab


class NativeAPI(unittest.TestCase):
    def test_only_explicitly_allowed_not_found_is_pending(self):
        self.assertIsNone(
            lab.decode_api_response(
                '{"error":"not assigned yet"}\n404', missing_ok=True
            )
        )
        with self.assertRaisesRegex(RuntimeError, "404"):
            lab.decode_api_response("{}\n404")
        with self.assertRaisesRegex(RuntimeError, "500"):
            lab.decode_api_response("{}\n500", missing_ok=True)

    def test_success_requires_valid_json_when_nonempty(self):
        self.assertEqual(
            lab.decode_api_response('{"state":"RUNNING"}\n200'), {"state": "RUNNING"}
        )
        self.assertIsNone(lab.decode_api_response("\n204"))
        with self.assertRaises(json.JSONDecodeError):
            lab.decode_api_response("not-json\n200")


if __name__ == "__main__":
    unittest.main()
