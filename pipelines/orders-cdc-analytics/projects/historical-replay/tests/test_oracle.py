import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from oracle import verify


class SnapshotOracle(unittest.TestCase):
    def test_exact_and_wrong_version(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "customers.csv").write_text("1,region-1,2\n")
            (p / "orders.csv").write_text("1,1,601,2,false,1680000001\n")
            (p / "joined.csv").write_text("1,1,601,region-1,2,2,1680000001,2000002\n")
            self.assertEqual(verify(p)["rows"], 1)
            (p / "joined.csv").write_text("1,1,601,region-1,2,2,1680000001,2000003\n")
            with self.assertRaises(ValueError):
                verify(p)
