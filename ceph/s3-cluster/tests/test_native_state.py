"""A guest boot ID alone cannot prove its surviving daemons stayed alive."""

import copy
import unittest
from uuid import UUID

from native_state import survivors, validate


def fixture():
    osds = {
        str(i): {"uuid": str(UUID(int=i + 1)), "host": f"ceph{i // 3}"}
        for i in range(9)
    }
    guests = {}
    for i in range(3):
        names = [f"ceph-mon@ceph{i}.service", f"ceph-radosgw@rgw.ceph{i}.service"]
        if i < 2:
            names.append(f"ceph-mgr@ceph{i}.service")
        names += [f"ceph-osd@{n}.service" for n in range(i * 3, i * 3 + 3)]
        guests[str(i)] = {
            "boot_id": str(UUID(int=100 + i)),
            "units": {
                n: {
                    "MainPID": "10",
                    "ExecMainStartTimestampMonotonic": "123",
                    "ActiveState": "active",
                    "SubState": "running",
                    "NRestarts": "0",
                }
                for n in names
            },
        }
    return {
        "fsid": str(UUID(int=999)),
        "osds": osds,
        "guests": guests,
        "versions": {"overall": {"ceph version 20.2.5 (test) tentacle": 17}},
    }


class NativeWitness(unittest.TestCase):
    def test_daemon_restart_without_guest_reboot_is_rejected(self):
        before = fixture()
        after = copy.deepcopy(before)
        after["guests"]["0"]["boot_id"] = str(UUID(int=200))
        after["guests"]["1"]["units"]["ceph-mon@ceph1.service"][
            "ExecMainStartTimestampMonotonic"
        ] = "456"
        with self.assertRaisesRegex(RuntimeError, "surviving native process"):
            survivors(before, after, guest="0")

    def test_only_declared_guest_can_reboot(self):
        before = fixture()
        after = copy.deepcopy(before)
        with self.assertRaisesRegex(RuntimeError, "did not reboot"):
            survivors(before, after, guest="0")
        after["guests"]["0"]["boot_id"] = str(UUID(int=200))
        survivors(before, after, guest="0")
        after["guests"]["2"]["boot_id"] = str(UUID(int=202))
        with self.assertRaisesRegex(RuntimeError, "surviving guest"):
            survivors(before, after, guest="0")

    def test_new_disk_requires_a_new_native_osd_uuid(self):
        before = fixture()
        after = copy.deepcopy(before)
        with self.assertRaisesRegex(RuntimeError, "reused"):
            survivors(before, after, replaced_osd="0")
        after["osds"]["0"]["uuid"] = str(UUID(int=500))
        after["guests"]["0"]["units"]["ceph-osd@0.service"]["MainPID"] = "99"
        survivors(before, after, replaced_osd="0")
        after["osds"]["4"]["uuid"] = str(UUID(int=504))
        with self.assertRaisesRegex(RuntimeError, "unrelated native OSD"):
            survivors(before, after, replaced_osd="0")

    def test_missing_service_duplicate_osd_and_mixed_version_fail(self):
        for mutate in [
            lambda v: v["guests"]["0"]["units"].pop("ceph-mon@ceph0.service"),
            lambda v: v["osds"]["1"].update(uuid=v["osds"]["0"]["uuid"]),
            lambda v: v.update(versions={"overall": {"ceph version 20.2.4": 17}}),
        ]:
            value = fixture()
            mutate(value)
            with self.assertRaises(RuntimeError):
                validate(value)
