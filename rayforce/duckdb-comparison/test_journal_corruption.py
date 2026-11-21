"""Guard fault location and make sure the integrity oracle rejects wrong bodies."""

import struct
import tempfile
import unittest
from pathlib import Path

from journal_corruption import (
    HEADER,
    choose_acknowledged_insert,
    compare_complete_exports,
    frames,
    mutate,
)


def fixture():
    data = b"RAYJLG1\0" + struct.pack("<q", 1)
    acknowledged = {}
    for identity in range(1, 6):
        row = [identity, 7, 8, 9, 10, 11]
        source = (
            "(insert 'trades (list " + " ".join(f"[{v}]" for v in row) + "))"
        ).encode()
        payload = b"\xf3\0" + struct.pack("<q", len(source)) + source
        data += HEADER.pack(0xCEFADEFA, 3, 0, 0, 1, len(payload)) + payload
        if identity in (1, 3, 5):
            acknowledged[identity] = row
    return data, acknowledged


class InteriorFaultGuards(unittest.TestCase):
    def test_only_acknowledged_middle_insert_and_one_payload_byte_change(self):
        data, acknowledged = fixture()
        selected = choose_acknowledged_insert(data, acknowledged)
        self.assertEqual(selected["row"][0], 3)
        for mode in ("invalid-type", "valid-literal"):
            changed, fault = mutate(data, selected, mode)
            self.assertEqual(len(changed), len(data))
            self.assertEqual(frames(changed), frames(data))
            self.assertEqual(sum(a != b for a, b in zip(data, changed, strict=True)), 1)
            self.assertEqual(changed[fault["offset"]], fault["new_byte"])
            if mode == "valid-literal":
                self.assertEqual(fault["bits_changed"], 1)
                self.assertEqual(fault["expected_mutated_row"], [3, 7, 8, 9, 10, 10])

    def test_unknown_framing_and_insufficient_acknowledged_rows_fail_closed(self):
        data, acknowledged = fixture()
        for broken in (
            b"wrong-preamble",
            data[:-1],
            data + b"x",
            data[:20] + b"\x04" + data[21:],
        ):
            with self.subTest(broken=broken[:25]), self.assertRaises(ValueError):
                frames(broken)
        with self.assertRaises(ValueError):
            choose_acknowledged_insert(data, {1: acknowledged[1]})

    def test_full_export_comparison_catches_body_and_id_errors(self):
        header = "id,customer,symbol,day,qty,price\n"
        with tempfile.TemporaryDirectory() as directory:
            good, bad = Path(directory) / "good.csv", Path(directory) / "bad.csv"
            good.write_text(header + "3,7,8,9,10,11\n")
            bad.write_text(header + "3,7,8,9,10,10\n")
            result = compare_complete_exports(good, bad)
            self.assertEqual(result["wrong_bodies"], 1)
            self.assertEqual(result["samples"][0]["actual"][-1], 10)
            for body in (header, header + "4,7,8,9,10,11\n"):
                bad.write_text(body)
                with self.assertRaises(RuntimeError):
                    compare_complete_exports(good, bad)


if __name__ == "__main__":
    unittest.main()
