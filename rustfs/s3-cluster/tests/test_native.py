"""Reject native health/repair shortcuts, without a running cluster."""

import copy
import unittest
from uuid import UUID

from native_health import heal_complete, member_identity, replacement_complete


class CanonicalHeal(unittest.TestCase):
    def result(self):
        return {
            "summary": "finished",
            "outcome": {
                "execution": {"state": "completed"},
                "coverage": "complete",
                "counters": {
                    "processed": 10,
                    "healed": 1,
                    "unchanged": 9,
                    "failed": 0,
                    "skipped": 0,
                    "unknown": 0,
                    "attemptFailures": 1,
                    "overflowed": False,
                },
            },
        }

    def test_legacy_finished_does_not_prove_repair(self):
        with self.assertRaisesRegex(RuntimeError, "omitted canonical"):
            heal_complete({"summary": "finished", "objectsHealed": 10000})
        self.assertFalse(heal_complete({"summary": "running"}))

    def test_unknown_partial_or_overflow_is_not_completion(self):
        for key, value in [
            ("unknown", 1),
            ("failed", 1),
            ("skipped", 1),
            ("overflowed", True),
        ]:
            result = self.result()
            result["outcome"]["counters"][key] = value
            with self.assertRaisesRegex(RuntimeError, "unresolved"):
                heal_complete(result)
        result = self.result()
        result["outcome"]["coverage"] = "partial"
        with self.assertRaisesRegex(RuntimeError, "partial"):
            heal_complete(result)

    def test_canonical_partition_and_recorded_native_retries(self):
        self.assertTrue(heal_complete(self.result()))
        result = self.result()
        result["outcome"]["counters"]["processed"] = 11
        with self.assertRaisesRegex(RuntimeError, "partition"):
            heal_complete(result)

    def test_format_completion_does_not_prove_object_scan_coverage(self):
        result = self.result()
        for key in ["processed", "healed", "unchanged", "attemptFailures"]:
            result["outcome"]["counters"][key] = 0
        self.assertTrue(heal_complete(result))
        with self.assertRaisesRegex(RuntimeError, "fixed ACK prefix"):
            heal_complete(result, minimum_objects=1)
        with self.assertRaisesRegex(RuntimeError, "fixed ACK prefix"):
            heal_complete(self.result(), minimum_objects=11)
        self.assertTrue(heal_complete(self.result(), minimum_objects=10))


class ReplacementProof(unittest.TestCase):
    def value(self):
        return {
            "contractVersion": 2,
            "scope": "cluster_survivor_disks",
            "localBlocked": [],
            "localDiagnostics": {
                "definitive": True,
                "rootBlocked": [],
                "checkpointBlocked": [],
            },
            "cluster": {
                "definitive": True,
                "reason": None,
                "records": [
                    {
                        "taskId": "new",
                        "generation": "new",
                        "setDiskId": "pool0/set0",
                        "targetSlots": ["http://rustfs0:9000/data/disk0"],
                        "state": "completed",
                        "reason": None,
                        "verifiedAt": 123,
                    }
                ],
            },
        }

    def test_old_or_unknown_generation_cannot_prove_new_rebuild(self):
        v = self.value()
        self.assertTrue(
            replacement_complete(
                v, endpoint="http://rustfs0:9000/data/disk0", previous=["old"]
            )
        )
        self.assertFalse(
            replacement_complete(
                v, endpoint="http://rustfs0:9000/data/disk0", previous=["new"]
            )
        )
        for state in ["running", "unknown", "cleanup_pending", "unrecoverable"]:
            changed = copy.deepcopy(v)
            changed["cluster"]["records"][0]["state"] = state
            self.assertFalse(replacement_complete(changed))

    def test_partial_peer_inventory_or_blocked_checkpoint_is_not_complete(self):
        v = self.value()
        v["cluster"]["definitive"] = False
        self.assertFalse(replacement_complete(v))
        v = self.value()
        v["localDiagnostics"]["definitive"] = False
        self.assertFalse(replacement_complete(v))
        v = self.value()
        v["localDiagnostics"]["checkpointBlocked"] = ["held"]
        self.assertFalse(replacement_complete(v))


class NativeMembership(unittest.TestCase):
    def views(self):
        servers = []
        for i in range(4):
            servers.append(
                {
                    "endpoint": f"rustfs{i}:9000",
                    "state": "online",
                    "drives": [
                        {
                            "uuid": str(UUID(int=i + 1)),
                            "state": "ok",
                            "healing": False,
                            "pool_index": 0,
                            "set_index": 0,
                            "disk_index": i,
                            "endpoint": f"http://rustfs{i}:9000/data/disk0",
                            "path": "/data/disk0",
                        }
                    ],
                }
            )
        view = {
            "bitrotSelftest": "passed",
            "ready": {
                "ready": True,
                "details": {
                    **{k: {"ready": True} for k in ["lock", "iam", "poolMetadata"]},
                    "storage": {"ready": True, "readQuorum": True, "writeQuorum": True},
                },
            },
            "integrity": {
                k: True
                for k in [
                    "local_reader_supported",
                    "write_requested",
                    "fleet_operator_attested",
                    "new_writes_enabled_here",
                ]
            },
            "info": {
                "deploymentID": str(UUID(int=100)),
                "servers": servers,
                "backend": {
                    "backendType": "Erasure",
                    "onlineDisks": 4,
                    "offlineDisks": 0,
                    "unknownDisks": 0,
                    "standardSCParity": 2,
                    "totalSets": [1],
                    "totalDrivesPerSet": [4],
                },
                "pools": {"0": {"0": {"healDisks": 0}}},
            },
        }
        return {f"rustfs{i}": copy.deepcopy(view) for i in range(4)}

    def test_missing_or_unknown_member_is_not_healthy(self):
        views = self.views()
        del views["rustfs3"]
        with self.assertRaisesRegex(RuntimeError, "missing native"):
            member_identity(views)
        views = self.views()
        views["rustfs0"]["info"]["backend"]["unknownDisks"] = 1
        self.assertIsNone(member_identity(views))
        views = self.views()
        views["rustfs0"]["ready"]["details"]["storage"]["writeQuorum"] = False
        self.assertIsNone(member_identity(views))
        views = self.views()
        views["rustfs0"]["info"]["servers"][0]["drives"][0]["uuid"] = ""
        self.assertIsNone(member_identity(views))

    def test_wrong_native_slot_or_unverified_bitrot_fails(self):
        views = self.views()
        views["rustfs0"]["info"]["servers"][0]["drives"][0]["disk_index"] = 3
        with self.assertRaisesRegex(RuntimeError, "wrong pool"):
            member_identity(views)
        views = self.views()
        views["rustfs0"]["bitrotSelftest"] = "unknown"
        with self.assertRaisesRegex(RuntimeError, "bitrot"):
            member_identity(views)

    def test_inconsistent_identity_and_active_rebuild_do_not_pass(self):
        views = self.views()
        self.assertIsNotNone(member_identity(views, repaired=True))
        views["rustfs0"]["info"]["servers"][0]["drives"][0]["uuid"] = str(UUID(int=99))
        self.assertIsNone(member_identity(views))
        views = self.views()
        views["rustfs0"]["info"]["servers"][0]["drives"][0]["healing"] = True
        self.assertIsNone(member_identity(views, repaired=True))
