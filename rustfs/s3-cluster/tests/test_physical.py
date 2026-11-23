"""Physical evidence must name the exact native slot and every retained part."""

import json
import tempfile
import unittest
from pathlib import Path
from uuid import UUID

from physical import files, fingerprints, formats, missing_metadata


class PhysicalProof(unittest.TestCase):
    def test_inconsistent_or_wrong_native_slot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = [str(UUID(int=i + 1)) for i in range(4)]
            for i in range(4):
                p = root / str(i) / "disk0/.rustfs.sys/format.json"
                p.parent.mkdir(parents=True)
                p.write_text(
                    json.dumps(
                        {
                            "version": "1",
                            "format": "xl",
                            "id": str(UUID(int=100)),
                            "xl": {
                                "version": "3",
                                "this": ids[i],
                                "sets": [ids],
                                "distributionAlgo": "SIPMOD+PARITY",
                            },
                        }
                    )
                )
            self.assertEqual(len(formats(root)["disks"]), 4)
            p = root / "3/disk0/.rustfs.sys/format.json"
            value = json.loads(p.read_text())
            value["xl"]["this"] = ids[0]
            p.write_text(json.dumps(value))
            with self.assertRaisesRegex(RuntimeError, "wrong persisted slot"):
                formats(root)

    def test_corruption_in_any_slot_changes_the_retained_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = "data/large/one"
            for i in range(4):
                p = root / str(i) / "disk0/lab-objects" / key / "native-version/part.1"
                p.parent.mkdir(parents=True)
                p.write_bytes(f"shard position {i}".encode())
            before = fingerprints({key}, root)
            p = root / "2/disk0/lab-objects" / key / "native-version/part.1"
            p.write_bytes(b"corrupted")
            after = fingerprints({key}, root)
            self.assertNotEqual(before[key]["2"], after[key]["2"])
            for i in ["0", "1", "3"]:
                self.assertEqual(before[key][i], after[key][i])
            self.assertEqual(files(key, 2, root), after[key]["2"])

    def test_missing_metadata_is_not_four_copy_presence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = "data/inline"
            for i in range(4):
                p = root / str(i) / "disk0/lab-objects" / key / "xl.meta"
                p.parent.mkdir(parents=True)
                p.write_bytes(b"opaque native metadata")
            self.assertEqual(missing_metadata({key}, root), [])
            (root / "1/disk0/lab-objects" / key / "xl.meta").unlink()
            self.assertEqual(missing_metadata({key}, root), [[key, 1]])
