import json
import unittest
from unittest.mock import patch

import control


class Guardrails(unittest.TestCase):
    def test_foreign_container_is_never_adopted(self):
        value = [
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "someone-else",
                        "com.docker.compose.service": "s0n0",
                    }
                }
            }
        ]
        with (
            patch("control.compose", return_value="id"),
            patch("control.run", return_value=json.dumps(value)),
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                control.owned("s0n0")

    def test_legacy_tables_are_not_reset_or_adopted(self):
        with (
            patch("control.validate_routing_config"),
            patch(
                "control.identities", return_value=({}, {"0": "1", "1": "2", "2": "3"})
            ),
            patch("control.primary", return_value="s0n0"),
            patch("control.sql", return_value="t"),
            patch("control.exclusive_save") as save,
        ):
            with self.assertRaisesRegex(RuntimeError, "legacy tables"):
                control.setup()
            save.assert_not_called()

    def test_disabled_bulk_rewrite_is_rejected_before_writes(self):
        with patch(
            "control.Path.read_text",
            return_value='[rewrite]\nenabled = false\nsplit_inserts = "rewrite"\n',
        ):
            with self.assertRaisesRegex(RuntimeError, "native PgDog rewrite"):
                control.validate_routing_config()
