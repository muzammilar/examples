"""A reused container ID does not prove a surviving process stayed alive."""

import unittest
from copy import deepcopy
from unittest.mock import patch

from control import SERVICES, check_survivors, data_target, distributions, volumes


class Guards(unittest.TestCase):
    def test_restart_is_rejected_even_when_container_id_is_unchanged(self):
        before = {
            name: {
                "Id": name,
                "RestartCount": 0,
                "State": {"Running": True, "StartedAt": "old", "Pid": 1},
            }
            for name in SERVICES
        }
        for field in ["StartedAt", "Pid"]:
            after = deepcopy(before)
            after["etcd1"]["State"][field] = "changed"
            with self.assertRaisesRegex(RuntimeError, "survivor"):
                check_survivors(before, after, "volume0")

    def test_nearest_rank_p99_on_small_sample_is_maximum(self):
        self.assertEqual(
            distributions([{"seconds": i} for i in range(1, 21)])["p99_ms"], 20000
        )


class Ownership(unittest.TestCase):
    def test_unlabelled_non_anonymous_volume_is_rejected(self):
        container = {"owned": {"Mounts": [{"Type": "volume", "Name": "owned-mount"}]}}
        with (
            patch(
                "control.run",
                return_value='[{"Name":"owned-mount","Labels":{},"CreatedAt":"now"}]',
            ),
            self.assertRaisesRegex(RuntimeError, "foreign volume"),
        ):
            volumes(container)

    def test_image_anonymous_mount_is_witnessed_from_its_owner(self):
        container = {"owned": {"Mounts": [{"Type": "volume", "Name": "owned-mount"}]}}
        with patch(
            "control.run",
            return_value='[{"Name":"owned-mount","Labels":{"com.docker.volume.anonymous":""},"CreatedAt":"now"}]',
        ):
            self.assertEqual(volumes(container), {"owned-mount": "now"})


class DataOwner(unittest.TestCase):
    def test_empty_server_is_not_a_valid_fault_target(self):
        placement = {
            "1": [
                {"node": 0, "files": 0},
                {"node": 2, "files": 10},
                {"node": 3, "files": 10},
                {"node": 6, "files": 10},
            ]
        }
        self.assertEqual(data_target(placement), "volume2")
        with self.assertRaisesRegex(RuntimeError, "no known nonempty"):
            data_target({})
