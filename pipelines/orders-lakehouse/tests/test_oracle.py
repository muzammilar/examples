"""Reject silent wrong answers/placement, not just incorrect row totals."""

from array import array
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lake-oracle"))
from rows import check_row


class RowOracle(unittest.TestCase):
    def setUp(self):
        self.journals = {
            "baseline": (array("q", [-1, 42]), bytearray([0, 1]), bytearray(2))
        }
        self.row = ("baseline", 1, 1, 101, "region-1", 1, 1, 1700000001, 1, 42, 1)

    def test_exact_row_and_duplicate(self):
        check_row(self.row, ("baseline", 1), self.journals, 10000)
        with self.assertRaises(ValueError):
            check_row(self.row, ("baseline", 1), self.journals, 10000)

    def test_each_field_corruption(self):
        for field in range(len(self.row)):
            with self.subTest(field=field):
                row = list(self.row)
                row[field] = "wrong" if isinstance(row[field], str) else row[field] + 1
                with self.assertRaises(ValueError):
                    check_row(tuple(row), ("baseline", 1), self.journals, 10000)

    def test_wrong_native_partition(self):
        for partition in (("baseline", 0), ("live", 1)):
            with self.assertRaises(ValueError):
                check_row(self.row, partition, self.journals, 10000)

    def test_unacknowledged_source(self):
        self.journals["baseline"][0][1] = -1
        with self.assertRaises(ValueError):
            check_row(self.row, ("baseline", 1), self.journals, 10000)


if __name__ == "__main__":
    unittest.main()
