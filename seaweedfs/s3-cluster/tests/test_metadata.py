"""A common historical hash must not conceal a stale metadata member."""

import unittest
from copy import deepcopy

from scripts.metadata_audit import validate


class CurrentMetadata(unittest.TestCase):
    def test_equal_old_hashes_do_not_qualify_a_lagging_member(self):
        values = [
            {
                "HashKV": {
                    "hash": 123,
                    "hash_revision": 10,
                    "header": {
                        "revision": 10,
                        "member_id": i,
                        "cluster_id": 9,
                        "raft_term": 3,
                    },
                }
            }
            for i in range(3)
        ]
        validate(values, 10)
        changed = deepcopy(values)
        changed[0]["HashKV"]["header"]["revision"] = 11
        with self.assertRaisesRegex(RuntimeError, "historical hashes"):
            validate(changed, 10)
        changed = deepcopy(values)
        changed[0]["HashKV"]["hash"] = 999
        with self.assertRaisesRegex(RuntimeError, "replicas differ"):
            validate(changed, 10)
