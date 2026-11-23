"""A preserved container ID does not prove a surviving process stayed alive."""

import unittest
from copy import deepcopy
from unittest.mock import patch
from control import SERVICES, check_survivors, check_disk_change, validate_mounts
from cold_reopen import ensure_idle


class ColdRestartAdmission(unittest.TestCase):
    def test_owned_one_off_workload_prevents_restart(self):
        with patch("control.run", return_value="writer-container\n") as run:
            with self.assertRaisesRegex(RuntimeError, "one-off workload"):
                ensure_idle()
            args = run.call_args.args[0]
            self.assertIn("label=com.docker.compose.oneoff=True", args)
            self.assertTrue(
                any(v.startswith("label=com.docker.compose.project=") for v in args)
            )

    def test_no_owned_one_off_workload_allows_restart(self):
        with patch("control.run", return_value=""):
            ensure_idle()


class Survivors(unittest.TestCase):
    def test_same_container_new_process_fails(self):
        before = {
            n: {
                "Id": n,
                "RestartCount": 0,
                "State": {"Running": True, "StartedAt": "old", "Pid": 1},
            }
            for n in SERVICES
        }
        after = deepcopy(before)
        after["rustfs1"]["State"]["Pid"] = 2
        with self.assertRaisesRegex(RuntimeError, "survivor"):
            check_survivors(before, after, "rustfs0")

    def test_only_declared_disk_slot_may_change_identity(self):
        before = {
            "deployment": "same",
            "disks": {f"rustfs{i}": str(i) for i in range(4)},
        }
        check_disk_change(before, deepcopy(before), "disk", "rustfs0")
        after = deepcopy(before)
        after["disks"]["rustfs0"] = "replacement"
        check_disk_change(before, after, "disk", "rustfs0")
        after["disks"]["rustfs1"] = "unexpected"
        with self.assertRaisesRegex(RuntimeError, "UUID transition"):
            check_disk_change(before, after, "disk", "rustfs0")


class DriveMounts(unittest.TestCase):
    def test_parent_only_directory_is_rejected_before_seeding(self):
        nodes = {
            f"rustfs{i}": {
                "Mounts": [
                    {"Type": "volume", "Name": f"data{i}0", "Destination": "/data"},
                    {
                        "Type": "volume",
                        "Name": f"data{i}0",
                        "Destination": "/data/disk0",
                    },
                ]
            }
            for i in range(4)
        }
        validate_mounts(nodes)
        nodes["rustfs2"]["Mounts"].pop()
        with self.assertRaisesRegex(RuntimeError, "direct mount"):
            validate_mounts(nodes)
