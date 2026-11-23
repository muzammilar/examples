"""Same-zone wrong-node placement must fail despite having three zone copies."""

import unittest

import msgpack
from placement import MARKER, decode_layout, owners_of, placement_errors


class NativePlacement(unittest.TestCase):
    def layout(self):
        nodes = [bytes([i]) * 32 for i in range(9)]
        peers = [n.hex() + "@garage" + str(i) + ":3901" for i, n in enumerate(nodes)]
        value = {
            "versions": [
                {
                    "version": 1,
                    "node_id_vec": nodes,
                    "ring_assignment_data": bytes([0, 3, 6]) * 256,
                    "replication_factor": 3,
                    "nongateway_node_count": 9,
                }
            ]
        }
        return peers, value

    def test_exact_native_assignment(self):
        peers, value = self.layout()
        ring = decode_layout(MARKER + msgpack.packb(value, use_bin_type=True), peers)
        self.assertEqual(owners_of("f0" + "00" * 31, ring), {0, 3, 6})
        self.assertEqual(
            placement_errors({"00" * 32}, {"00" * 32: [0, 3, 6]}, ring), ([], [])
        )

    def test_three_zones_are_insufficient_when_owner_is_wrong(self):
        ring = {"owners": [[0, 3, 6]] * 256}
        block = "00" * 32
        self.assertEqual(
            placement_errors({block}, {block: [1, 3, 6]}, ring), ([block], [block])
        )

    def test_unknown_marker_and_transitional_layout_fail(self):
        peers, value = self.layout()
        with self.assertRaisesRegex(RuntimeError, "marker"):
            decode_layout(b"future-format", peers)
        value["versions"] *= 2
        with self.assertRaisesRegex(RuntimeError, "transitional"):
            decode_layout(MARKER + msgpack.packb(value, use_bin_type=True), peers)

    def test_invalid_ring_index_and_changed_member_fail(self):
        peers, value = self.layout()
        value["versions"][0]["ring_assignment_data"] = bytes([99, 3, 6]) * 256
        with self.assertRaisesRegex(RuntimeError, "index"):
            decode_layout(MARKER + msgpack.packb(value, use_bin_type=True), peers)
        peers[0] = "ff" * 32 + "@unknown"
        with self.assertRaisesRegex(RuntimeError, "membership"):
            decode_layout(MARKER + msgpack.packb(value, use_bin_type=True), peers)
