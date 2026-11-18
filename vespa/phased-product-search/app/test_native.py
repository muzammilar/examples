import json
from pathlib import Path
import tempfile
import unittest

from native import canonical_metadata, read_copy


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

    def test_root_type_extra_reply_and_truncated_tail_are_rejected(self):
        doc = {"id": "id:catalog:product::0", "fields": {"pid": 0}}
        for text in [
            json.dumps({"item": doc}),
            json.dumps([doc, None]),
            "[" + json.dumps(doc) + ",",
        ]:
            with self.assertRaises(RuntimeError):
                self.read(text, 1)

    def test_reply_must_match_requested_position(self):
        for document in [
            {"id": "id:catalog:product::1", "fields": {"pid": 1}},
            {"id": "id:catalog:product::0", "fields": {"pid": 1}},
        ]:
            with self.assertRaisesRegex(RuntimeError, "requested ID/position"):
                self.read(json.dumps([document]), 1)


class MetadataTests(unittest.TestCase):
    def test_only_missing_empty_strings_have_equivalent_native_representation(self):
        expected = {"id": "0", "name": "product", "description": "", "version": 0}
        fields = {"pid": 0, "name": "product", "version": 0, "embedding": {}}
        self.assertEqual(canonical_metadata(fields, expected), expected)
        for missing in ["name", "version"]:
            bad = fields.copy()
            bad.pop(missing)
            with self.assertRaises(RuntimeError):
                canonical_metadata(bad, expected)
        for bad in [fields | {"version": False}, fields | {"extra": 1}]:
            with self.assertRaises(RuntimeError):
                canonical_metadata(bad, expected)
