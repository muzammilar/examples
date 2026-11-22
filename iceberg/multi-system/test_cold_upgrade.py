"""An upgraded restore must never reopen implicitly with its older engine."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import cold_upgrade as upgrade
import cold_readback as readback


class ImageTransition(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name).resolve() / str(uuid.uuid4())
        self.parent.mkdir()
        self.old = readback.BASELINE_453_IMAGE
        self.invocation = {"services": {"storage": {"image_id": self.old}}}
        self.volume = {
            "Name": "restore-owned",
            "CreatedAt": "2026-11-22T00:00:00Z",
            "Mountpoint": "/owned/data",
        }
        self.token = str(uuid.uuid4())
        self.intent = {
            "checkpoint_id": self.parent.name,
            "transition_id": self.token,
            "restore_volume": self.volume,
            "source_image": self.old,
            "target_image": readback.UPGRADE_454_IMAGE,
            "target_version": "4.54",
        }

    def write(self, changes=None):
        (self.parent / "storage-image-transition.json").write_text(
            json.dumps(self.intent | (changes or {}))
        )

    def test_unreserved_checkpoint_defaults_to_original_image(self):
        self.assertEqual(
            readback.storage_selection(self.parent, self.invocation, self.volume),
            (self.old, None),
        )
        with self.assertRaises(RuntimeError):
            readback.storage_selection(
                self.parent,
                self.invocation,
                self.volume,
                readback.UPGRADE_454_IMAGE,
                self.token,
            )

    def test_registered_upgrade_is_bound_to_image_volume_and_token(self):
        self.write()
        image, reference = readback.storage_selection(
            self.parent,
            self.invocation,
            self.volume,
            readback.UPGRADE_454_IMAGE,
            self.token,
        )
        self.assertEqual(image, readback.UPGRADE_454_IMAGE)
        self.assertEqual(reference["transition_id"], self.token)
        for image, token in (
            (self.old, self.token),
            (readback.UPGRADE_454_IMAGE, str(uuid.uuid4())),
        ):
            with (
                self.subTest(image=image, token=token),
                self.assertRaises(RuntimeError),
            ):
                readback.storage_selection(
                    self.parent, self.invocation, self.volume, image, token
                )
        self.write({"restore_volume": self.volume | {"CreatedAt": "recreated"}})
        with self.assertRaises(RuntimeError):
            readback.storage_selection(
                self.parent,
                self.invocation,
                self.volume,
                readback.UPGRADE_454_IMAGE,
                self.token,
            )

    def test_marker_blocks_default_even_if_upgrade_never_finished(self):
        self.write()
        with self.assertRaisesRegex(RuntimeError, "refuse implicit downgrade"):
            readback.storage_selection(self.parent, self.invocation, self.volume)

    def test_source_and_target_revision_are_not_interchangeable(self):
        for changes in (
            {"source_image": "sha256:" + "b" * 64},
            {"target_version": "4.55"},
            {"checkpoint_id": str(uuid.uuid4())},
        ):
            with self.subTest(changes=changes):
                self.write(changes)
                with self.assertRaises(RuntimeError):
                    readback.storage_selection(
                        self.parent,
                        self.invocation,
                        self.volume,
                        readback.UPGRADE_454_IMAGE,
                        self.token,
                    )

    def test_symlinked_transition_is_not_read(self):
        target = self.parent.parent / "outside.json"
        target.write_text(json.dumps(self.intent))
        (self.parent / "storage-image-transition.json").symlink_to(target)
        with self.assertRaises(RuntimeError):
            readback.storage_selection(
                self.parent,
                self.invocation,
                self.volume,
                readback.UPGRADE_454_IMAGE,
                self.token,
            )

    def test_cleanup_accepts_only_the_hash_bound_upgrade_image(self):
        import cold_cleanup

        self.write()
        output = self.parent / "native-result"
        output.mkdir()
        (output / "storage-image-transition.json").write_bytes(
            (self.parent / "storage-image-transition.json").read_bytes()
        )
        image, reference = readback.storage_selection(
            self.parent,
            self.invocation,
            self.volume,
            readback.UPGRADE_454_IMAGE,
            self.token,
        )
        invocation = {"storage_image": image, "storage_image_intent": reference}
        self.assertEqual(
            cold_cleanup.storage_image(
                output, self.parent, self.invocation, self.volume, invocation
            ),
            image,
        )
        (output / "storage-image-transition.json").write_text("changed")
        with self.assertRaises(RuntimeError):
            cold_cleanup.storage_image(
                output, self.parent, self.invocation, self.volume, invocation
            )
        with self.assertRaises(RuntimeError):
            cold_cleanup.storage_image(
                output,
                self.parent,
                self.invocation,
                self.volume,
                {"storage_image": image},
            )

    def test_unknown_source_cannot_accidentally_downgrade_to_454(self):
        self.invocation["source_volume"] = {"Name": "source"}
        self.invocation["services"]["storage"]["image_id"] = "sha256:" + "f" * 64
        with (
            patch.object(upgrade.control, "local_docker"),
            patch.object(
                upgrade.readback,
                "checkpoint",
                return_value=(self.parent, self.invocation, self.volume),
            ),
            patch.object(upgrade.readback, "fixture"),
            patch.object(upgrade.control, "call") as call,
            self.assertRaisesRegex(RuntimeError, "pinned 4.53 to 4.54"),
        ):
            upgrade.prepare(self.parent, "case", "verification")
        call.assert_not_called()
        self.assertFalse((self.parent / "storage-image-transition.json").exists())

    def test_bad_case_is_rejected_before_image_access_or_reservation(self):
        with (
            patch.object(upgrade.control, "local_docker"),
            patch.object(
                upgrade.readback,
                "checkpoint",
                return_value=(self.parent, self.invocation, self.volume),
            ),
            patch.object(
                upgrade.readback, "fixture", side_effect=RuntimeError("bad witness")
            ),
            patch.object(upgrade.control, "call") as call,
            self.assertRaises(RuntimeError),
        ):
            self.invocation["source_volume"] = {"Name": "source"}
            upgrade.prepare(self.parent, "case", "verification")
        call.assert_not_called()
        self.assertFalse((self.parent / "storage-image-transition.json").exists())


if __name__ == "__main__":
    unittest.main()
