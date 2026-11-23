"""Persistence adapter confinement and pinned metadata negative controls."""

from pathlib import Path
import struct
import tempfile
import unittest

import experiment
from persistence import encoded_headers


class PersistenceGates(unittest.TestCase):
    def test_database_paths_cannot_escape_the_owned_witness_mount(self):
        for path in [
            "/data/input.duckdb",
            "/verification/../outside",
            "/verification",
            "relative",
        ]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                experiment.runtime_script("cpp", "duckdb", "plan", 1, path)
        with self.assertRaises(ValueError):
            experiment.runtime_script("cpp", "rayforce", "plan", 1, "/verification/db")
        command = experiment.runtime_script(
            "cpp", "duckdb", "plan", 1, "/verification/state.duckdb"
        )
        self.assertIn("/verification/state.duckdb", command)

    def test_encoded_metadata_rejects_wrong_row_count_and_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for table, columns, count in [
                ("trades", ["id", "customer", "symbol", "day", "qty", "price"], 2),
                ("customers", ["customer", "region", "factor"], 10000),
            ]:
                (root / table).mkdir()
                for column in columns:
                    header = bytearray(96)
                    header[17], header[18] = 2, 5
                    struct.pack_into("<q", header, 24, count)
                    header[32:36] = b"RENC"
                    (root / table / column).write_bytes(header)
            self.assertEqual(len(encoded_headers(root, 2)), 9)
            with self.assertRaisesRegex(RuntimeError, "column header"):
                encoded_headers(root, 3)
            path = root / "trades/id"
            header = bytearray(path.read_bytes())
            header[17] = 0
            path.write_bytes(header)
            with self.assertRaisesRegex(RuntimeError, "column header"):
                encoded_headers(root, 2)


if __name__ == "__main__":
    unittest.main()
