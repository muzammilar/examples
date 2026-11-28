"""Destructive recovery must reject stale identity and still-attached disks."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

NATIVE = Path(__file__).resolve().parents[1] / "native"
sys.path.insert(0, str(NATIVE))
import storage_fault


class RecoveryGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.value = dict(
            fsid=str(uuid.uuid4()),
            osd=0,
            osd_fsid=str(uuid.uuid4()),
            new_osd_fsid=str(uuid.uuid4()),
            disk=0,
            spare=3,
        )
        storage_fault.save(self.root / "replacement.json", self.value)

    def test_foreign_cluster_cannot_reuse_plan(self):
        with patch.object(storage_fault, "STATE", self.root), patch.object(
            storage_fault, "ceph", return_value={"fsid": str(uuid.uuid4())}
        ):
            with self.assertRaisesRegex(RuntimeError, "another cluster"):
                storage_fault.plan()

    def test_attached_original_cannot_be_destroyed(self):
        with patch.object(storage_fault, "plan", return_value=self.value), patch.object(
            storage_fault, "device", return_value="/dev/sda"
        ), patch.object(storage_fault, "ssh") as ssh:
            with self.assertRaisesRegex(RuntimeError, "still attached"):
                storage_fault.replace_disk()
            ssh.assert_not_called()

    def test_unexpected_replacement_identity_cannot_be_activated(self):
        with patch.object(
            storage_fault, "device", side_effect=["/dev/sdb", None]
        ), patch.object(
            storage_fault,
            "claims",
            return_value=[(0, {"tags": {"ceph.osd_fsid": str(uuid.uuid4())}})],
        ):
            with self.assertRaisesRegex(RuntimeError, "identity differs"):
                storage_fault.verify_replacement(self.value)


if __name__ == "__main__":
    unittest.main()
