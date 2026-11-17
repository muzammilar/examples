"""Reject insufficient real capacity without allocating/filling test disks."""

import unittest

from capacity import GIB, RUNTIME_FLOOR, preflight, require_free


class Capacity(unittest.TestCase):
    def test_host_budget_and_actual_disk_are_both_required(self):
        info = {"Architecture": "aarch64", "MemTotal": 24 * GIB, "NCPU": 11}
        preflight(info, {"free_bytes": 26 * GIB})
        for key, value in [
            ("Architecture", "x86_64"),
            ("MemTotal", 20 * GIB),
            ("NCPU", 8),
        ]:
            with self.assertRaises(RuntimeError):
                preflight(info | {key: value}, {"free_bytes": 40 * GIB})
        with self.assertRaisesRegex(RuntimeError, "disk safety floor"):
            preflight(info, {"free_bytes": 24 * GIB})

    def test_running_lab_stops_above_shared_host_exhaustion(self):
        require_free({"free_bytes": RUNTIME_FLOOR}, RUNTIME_FLOOR)
        with self.assertRaisesRegex(RuntimeError, "disk safety floor"):
            require_free({"free_bytes": RUNTIME_FLOOR - 1}, RUNTIME_FLOOR)
