import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from publication import check_manifest


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.entry = dict(
            path="/orders/customer_shard=0/part-a.parquet",
            bytes=123,
            checksum="native-crc",
        )
        self.manifest = dict(
            batch="baseline",
            rows=10,
            customers=3,
            recipe="orders-v1-one-based",
            files=[self.entry],
        )
        self.actual = {"orders/customer_shard=0/part-a.parquet": 123}

    def test_exact_inventory(self):
        self.assertEqual(
            check_manifest(self.manifest, self.actual, "baseline", 10, 3), self.actual
        )

    def test_missing_file(self):
        with self.assertRaises(ValueError):
            check_manifest(self.manifest, {}, "baseline", 10, 3)

    def test_duplicate_hidden_by_dictionary(self):
        self.manifest["files"].append(dict(self.entry))
        with self.assertRaises(ValueError):
            check_manifest(self.manifest, self.actual, "baseline", 10, 3)

    def test_foreign_recipe_or_workload(self):
        with self.assertRaises(ValueError):
            check_manifest(self.manifest, self.actual, "baseline", 100, 3)

    def test_unsafe_path(self):
        self.entry["path"] = "/orders/../secrets.parquet"
        with self.assertRaises(ValueError):
            check_manifest(self.manifest, self.actual, "baseline", 10, 3)
