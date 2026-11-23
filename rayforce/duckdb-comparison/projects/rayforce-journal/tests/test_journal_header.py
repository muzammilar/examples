"""Keep the interior-fault location and unsafe-recovery oracle honest."""

import tempfile
from pathlib import Path
import unittest

from journal_corruption import choose_acknowledged_insert, frames
from journal_header import (
    compare_prefix_restore,
    damage_header,
    suffix_acknowledgements,
)
from test_journal_corruption import fixture


class HeaderFaultGuards(unittest.TestCase):
    def test_single_header_bit_changes_no_payload_or_suffix(self):
        data, acknowledged = fixture()
        selected = choose_acknowledged_insert(data, acknowledged)
        for mode in ("magic", "version"):
            with self.subTest(mode=mode):
                changed, fault = damage_header(data, selected, mode)
                self.assertEqual(len(changed), len(data))
                self.assertEqual(
                    sum(a != b for a, b in zip(data, changed, strict=True)), 1
                )
                self.assertEqual((fault["old_byte"] ^ fault["new_byte"]).bit_count(), 1)
                self.assertEqual(
                    changed[selected["payload_offset"] :],
                    data[selected["payload_offset"] :],
                )
                with self.assertRaises(ValueError):
                    frames(changed)
        self.assertEqual(
            suffix_acknowledgements(data, selected["offset"], acknowledged), {3, 5}
        )

    def test_loss_oracle_rejects_wrong_bodies_and_unpredicted_missing_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            good, cut = Path(directory) / "good.csv", Path(directory) / "cut.csv"
            good.write_text("id,price\n1,10\n2,20\n3,30\n")
            cut.write_text("id,price\n1,10\n")
            result = compare_prefix_restore(good, cut, {2, 3})
            self.assertEqual(result["acknowledged_loss"], 2)
            self.assertEqual(result["surviving_bodies_verified"], 1)
            for body in ("id,price\n1,11\n", "id,price\n", "id,price\n1,10\n2,20\n"):
                cut.write_text(body)
                with self.assertRaises(RuntimeError):
                    compare_prefix_restore(good, cut, {2, 3})


if __name__ == "__main__":
    unittest.main()
