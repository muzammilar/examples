import json
from pathlib import Path
import tempfile
import unittest

from native import read_copy


class NativeTests(unittest.TestCase):
    def read(self, text, count):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "native.json"
            path.write_text(text, encoding="utf-8")
            return read_copy(path, count)

    def test_native_absence_and_unicode_product_text(self):
        doc = {
            "id": "id:catalog:product::1",
            "fields": {"pid": 1, "name": "café\u2028Document not found."},
        }
        text = (
            "[\nDocument not found.\n,\n"
            + json.dumps(doc, ensure_ascii=False)
            + ",\nDocument not found.\n]"
        )
        result = self.read(text, 3)
        self.assertEqual(result, [None, doc, None])

    def test_error_reply_is_not_absence(self):
        # The CLI can exit successfully after printing a reply error on stderr;
        # its missing array element must still fail verification.
        for text in ["[,{}]", "[null,]", "[]"]:
            with self.assertRaises((ValueError, RuntimeError)):
                self.read(text, 2)

    def test_reply_must_match_requested_position(self):
        for document in [
            {"id": "id:catalog:product::1", "fields": {"pid": 1}},
            {"id": "id:catalog:product::0", "fields": {"pid": 1}},
        ]:
            with self.assertRaisesRegex(RuntimeError, "requested ID/position"):
                self.read(json.dumps([document]), 1)
