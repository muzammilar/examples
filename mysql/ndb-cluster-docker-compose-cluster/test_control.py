import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import control


class NativeGuards(unittest.TestCase):
    def fragment(self):
        return {
            "partition_id": "0",
            "fragment_id": "0",
            "no_of_replicas": "3",
            "current_primary": "1",
            "current_first_backup": "2",
            "current_second_backup": "3",
            "current_third_backup": "0",
            "num_alive_replicas": "3",
            "num_dead_replicas": "0",
        }

    def test_fragment_requires_all_correct_live_copies(self):
        self.assertEqual(control.validate_fragment(self.fragment()), (0, {1, 2, 3}))
        for field, value in (
            ("current_second_backup", "4"),
            ("num_alive_replicas", "2"),
            ("num_dead_replicas", "1"),
            ("fragment_id", "1"),
        ):
            with self.subTest(field=field):
                with self.assertRaises(RuntimeError):
                    control.validate_fragment(self.fragment() | {field: value})

    def test_degraded_state_never_qualifies_as_full_repair(self):
        value = self.fragment() | {
            "current_primary": "2",
            "current_first_backup": "3",
            "current_second_backup": "0",
            "num_alive_replicas": "2",
            "num_dead_replicas": "1",
            "no_of_replicas": "2",
        }
        self.assertEqual(
            control.validate_fragment(value, healthy=False), (0, {1, 2, 3})
        )
        with self.assertRaises(RuntimeError):
            control.validate_fragment(value)

    def test_foreign_container_rejected(self):
        value = [
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "foreign",
                        "com.docker.compose.service": "data1",
                    }
                }
            }
        ]
        with (
            patch("control.compose", return_value="id"),
            patch("control.run", return_value=json.dumps(value)),
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                control.owned("data1")

    def test_incomplete_volume_witness_rejected(self):
        identity = "18ed4662-94eb-4fbf-b959-1676b7411034"
        marker = {"id": identity, "customers": 10000, "subscribers": 1000000}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / identity
            folder.mkdir()
            (folder / "fixture.json").write_text(
                json.dumps(
                    marker
                    | {"physical": {"sql0": "a", "sql1": "b"}, "volumes": {"data1": {}}}
                )
            )
            with (
                patch("control.ROOT", root),
                patch(
                    "control.identities", return_value={"sql0": marker, "sql1": marker}
                ),
                patch("control.sql") as probe,
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete physical"):
                    control.fixture()
                probe.assert_not_called()

    def test_fixed_role_inventory(self):
        self.assertEqual(len(control.DATA), 9)
        self.assertEqual(len(control.SERVICES), 14)

    def test_successful_empty_optional_native_result(self):
        with patch("control.sql", return_value=""):
            self.assertEqual(
                control.rows("sql0", "SELECT * FROM ndbinfo.restart_info"), []
            )
