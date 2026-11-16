import json
import unittest
import tempfile
from pathlib import Path
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

    def test_sharded_configuration_rejected(self):
        with patch("control.ctl", return_value={"sharded": True}):
            with self.assertRaisesRegex(RuntimeError, "unsharded"):
                control.validate_vschema()

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

    def test_fixed_topology_retains_both_replicas(self):
        self.assertEqual(control.SHARDS, ("0",))
        self.assertEqual(control.TABLETS, {0: ("t100", "t101", "t102")})

    def test_incomplete_witness_rejected_before_physical_adoption(self):
        identity = "18ed4662-94eb-4fbf-b959-1676b7411034"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / identity
            folder.mkdir()
            (folder / "fixture.json").write_text(
                json.dumps({"customers": 10000, "physical": {"t100": "original"}})
            )
            with (
                patch("control.ROOT", root),
                patch(
                    "control.identities",
                    return_value={"0": {"id": identity, "customers": 10000}},
                ),
                patch("control.server_uuid") as probe,
            ):
                with self.assertRaisesRegex(RuntimeError, "incomplete physical"):
                    control.fixture()
                probe.assert_not_called()
