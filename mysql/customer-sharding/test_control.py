import json
import unittest
from unittest.mock import patch

import control


class NativeGuards(unittest.TestCase):
    def test_foreign_container_rejected(self):
        value = [
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "foreign",
                        "com.docker.compose.service": "t100",
                    }
                }
            }
        ]
        with (
            patch("control.compose", return_value="id"),
            patch("control.run", return_value=json.dumps(value)),
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                control.owned("t100")

    def test_absent_primary_is_unknown_not_a_tablet(self):
        with patch("control.ctl", return_value={"shard": {"primary_alias": None}}):
            with self.assertRaisesRegex(RuntimeError, "no valid native primary"):
                control.primary(0)

    def test_range_boundaries_and_ambiguous_ownership(self):
        ranges = [(b"", b"\x55"), (b"\x55", b"\xaa"), (b"\xaa", b"")]
        for value, expected in (
            (0, 0),
            (0x54, 0),
            (0x55, 1),
            (0xA9, 1),
            (0xAA, 2),
            (0xFF, 2),
        ):
            self.assertEqual(
                control.shard_for_key(bytes([value]) + b"\0" * 7, ranges), expected
            )
        with self.assertRaisesRegex(RuntimeError, "ambiguous"):
            control.shard_for_key(b"\x66" + b"\0" * 7, [(b"", b"\xaa"), (b"\x55", b"")])

    def test_legacy_tables_are_not_adopted(self):
        with (
            patch("control.primary", return_value="t100"),
            patch("control.identities", return_value={}),
            patch("control.sql", return_value="1"),
            patch("control.exclusive_save") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing reset/adoption"):
                control.setup()
            save.assert_not_called()
