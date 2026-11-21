"""Negative controls for persisted-history reconciliation, independent of native DB."""

import tempfile
import unittest
from pathlib import Path

from live_journal import reconcile


class JournalReconciliation(unittest.TestCase):
    def test_acknowledged_loss_wrong_bodies_duplicates_and_unknown_rows_fail(self):
        header = "id,customer,symbol,day,qty,price\n"
        seed_row = "0,2,3,4,5,6\n"
        row = [1, 7, 8, 9, 10, 11]
        good = "1,7,8,9,10,11\n"
        events = [
            {"kind": "intent", "id": 1, "row": row},
            {"kind": "write", "id": 1, "status": "ok"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed, output = Path(directory) / "seed.csv", Path(directory) / "out.csv"
            seed.write_text(header + seed_row)
            output.write_text(header + seed_row + good)
            self.assertEqual(reconcile(seed, output, events)["acknowledged"], 1)
            for body in [
                seed_row,
                seed_row + good + good,
                seed_row + good.replace("11", "12"),
                seed_row + good + "2,1,1,1,1,1\n",
                seed_row.replace("2", "3") + good,
            ]:
                with self.subTest(body=body), self.assertRaises(RuntimeError):
                    output.write_text(header + body)
                    reconcile(seed, output, events)

    def test_applied_ambiguous_intent_is_not_counted_as_acknowledged(self):
        header = "id,customer,symbol,day,qty,price\n"
        events = [
            {"kind": "intent", "id": 1, "row": [1, 2, 3, 4, 5, 6]},
            {"kind": "write", "id": 1, "status": "error"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            seed, output = Path(directory) / "seed.csv", Path(directory) / "out.csv"
            seed.write_text(header)
            output.write_text(header + "1,2,3,4,5,6\n")
            proof = reconcile(seed, output, events)
            self.assertEqual(proof["acknowledged"], 0)
            self.assertEqual(proof["applied_unacknowledged_intents"], 1)
            with self.assertRaisesRegex(RuntimeError, "reused"):
                reconcile(seed, output, events + [events[0]])
            with self.assertRaisesRegex(RuntimeError, "preceding intent"):
                reconcile(seed, output, [events[1]])


if __name__ == "__main__":
    unittest.main()
