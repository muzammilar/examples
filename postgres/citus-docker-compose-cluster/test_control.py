import json
import unittest
from unittest.mock import patch

import control


class NativeGuards(unittest.TestCase):
    def test_foreign_container_rejected(self):
        info = [
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "foreign",
                        "com.docker.compose.service": "s0n0",
                    }
                }
            }
        ]
        with (
            patch("control.compose", return_value="id"),
            patch("control.run", return_value=json.dumps(info)),
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                control.owned("s0n0")

    def test_legacy_tables_are_not_adopted(self):
        with (
            patch("control.groups", return_value=[0, 1, 2, 3]),
            patch("control.primary", return_value="s0n0"),
            patch("control.sql", side_effect=["f", "t"]),
            patch("control.exclusive_save") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "refusing reset/adoption"):
                control.setup()
            save.assert_not_called()

    def test_missing_duplicate_or_wrong_owner_shards_fail(self):
        rows = [
            {
                "orders": i + 100,
                "customers": i + 200,
                "profiles": i + 300,
                "group": i % 3 + 1,
            }
            for i in range(30)
        ]
        for broken in (
            rows[:-1],
            rows[:-1] + [rows[0]],
            [dict(row, group=9) for row in rows],
        ):
            with self.assertRaisesRegex(RuntimeError, "native shard placements"):
                control.validate_placements(broken, [1, 2, 3])
