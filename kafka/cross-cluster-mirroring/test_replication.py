import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import replication_control as control
import replication_lab as lab
import replication_report as report
import replication_resources as resources


def healthy_fixture():
    ids = {}
    workers = [lab.CLUSTER + suffix for suffix in ("-worker", "-worker2", "-worker3")]
    for side in ("east", "west"):
        for role, members in [("controllers", range(3)), ("brokers", range(3, 6))]:
            for index, member in enumerate(members):
                pod = f"{side}-{side}-{role}-{member}"
                ids[pod + "/kafka"] = {
                    "pod": pod,
                    "node": workers[index],
                    "ready": True,
                }
    for member in range(2):
        pod = f"replication-mirrormaker2-{member}"
        ids[pod + "/connect"] = {"pod": pod, "node": workers[member], "ready": True}
    partitions = [
        {
            "topic": "orders-1",
            "partition": p,
            "leader": 3,
            "replicas": [3, 4, 5],
            "isr": [3, 4, 5],
        }
        for p in range(6)
    ]
    side = {
        "partitions": partitions,
        "quorum": 'LeaderId: 0\nCurrentVoters: [{"id":0},{"id":1},{"id":2}]\nMaxFollowerLag: 0\n',
        "logs": {
            "brokers": [
                {
                    "broker": b,
                    "logDirs": [
                        {
                            "error": None,
                            "partitions": [
                                {
                                    "partition": f"orders-1-{p}",
                                    "offsetLag": 0,
                                    "isFuture": False,
                                }
                                for p in range(6)
                            ],
                        }
                    ],
                }
                for b in range(3, 6)
            ]
        },
    }
    east = copy.deepcopy(side)
    west = copy.deepcopy(side)
    east["quorum"] = "ClusterId: east-id\n" + east["quorum"]
    west["quorum"] = "ClusterId: west-id\n" + west["quorum"]
    return {
        "identities": ids,
        "sides": {"east": east, "west": west},
        "connect": {
            "east->west.MirrorSourceConnector": {
                "status": {
                    "connector": {"state": "RUNNING"},
                    "tasks": [{"id": i, "state": "RUNNING"} for i in range(3)],
                }
            },
            "east->west.MirrorCheckpointConnector": {
                "status": {"connector": {"state": "RUNNING"}, "tasks": []}
            },
        },
    }


class NativeTests(unittest.TestCase):
    def test_unqualified_operation_is_rejected_before_setup(self):
        with self.assertRaises(ValueError):
            control.Experiment("unsupported-operation")

    def test_requires_replicas_log_repair_quorum_and_tasks(self):
        original = healthy_fixture()
        self.assertTrue(control.healthy(original, {"orders-1"}))
        mutations = [
            lambda s: s["sides"]["west"]["partitions"][0].update(isr=[3, 4]),
            lambda s: s["sides"]["west"]["logs"]["brokers"][0]["logDirs"][0][
                "partitions"
            ][0].update(offsetLag=1),
            lambda s: s["sides"]["west"].update(
                quorum='LeaderId: 0\nCurrentVoters: [{"id":0}]\nMaxFollowerLag: 0\n'
            ),
            lambda s: s["connect"]["east->west.MirrorSourceConnector"]["status"][
                "tasks"
            ][0].update(state="FAILED"),
            lambda s: s["identities"].pop("replication-mirrormaker2-1/connect"),
            lambda s: s["identities"]["west-west-brokers-4/kafka"].update(
                node=lab.CLUSTER + "-worker"
            ),
        ]
        for mutate in mutations:
            sample = copy.deepcopy(original)
            mutate(sample)
            self.assertFalse(control.healthy(sample, {"orders-1"}))

    def test_rejects_missing_native_partition_metadata(self):
        with self.assertRaises(ValueError):
            control.partitions("nothing to inspect")

    def test_data_volume_must_be_writable_and_owned_by_exact_pod_uid(self):
        original = {
            "info": {
                "runtimeSpec": {
                    "mounts": [
                        {
                            "destination": "/var/lib/kafka/data",
                            "source": "/var/lib/kubelet/pods/owned/volumes/kubernetes.io~empty-dir/data",
                            "type": "bind",
                            "options": ["rw"],
                        }
                    ]
                }
            }
        }
        self.assertTrue(control.data_volume(original, "owned").endswith("/data"))
        with self.assertRaises(RuntimeError):
            control.data_volume(original, "other")
        changed = copy.deepcopy(original)
        changed["info"]["runtimeSpec"]["mounts"][0]["options"] = ["ro"]
        with self.assertRaises(RuntimeError):
            control.data_volume(changed, "owned")


class ReportTests(unittest.TestCase):
    def test_mirrored_offsets_differ_but_partition_and_uniqueness_must_hold(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            intent = {
                "stage": "intent",
                "seq": 0,
                "id": "run-order-0",
                "sha256": "ab" * 32,
                "time": 1,
            }
            ack = intent | {
                "stage": "ack",
                "time": 2,
                "seconds": 0.01,
                "partition": 0,
                "offset": 5,
            }
            observed = intent | {
                "stage": "message",
                "time": 3,
                "seconds": 0.01,
                "created_ms": 1000,
                "partition": 0,
                "offset": 50,
            }
            with gzip.open(root / "producer.jsonl.gz", "wt") as stream:
                stream.write(json.dumps(intent) + "\n" + json.dumps(ack) + "\n")

            def write(*records):
                with gzip.open(root / "reader.jsonl.gz", "wt") as stream:
                    for record in records:
                        stream.write(json.dumps(record) + "\n")

            phases = [
                {"phase": "baseline", "time": 0},
                {"phase": "complete", "time": 4},
            ]
            plan = {"run_id": "run", "minimum_messages": 100, "mirrored": True}
            write(observed)
            self.assertEqual(
                report.reconcile(root, plan, phases)["acknowledged_loss"], 0
            )
            for records in [
                (observed, observed | {"offset": 51}),
                (observed | {"partition": 1},),
                (observed | {"sha256": "00" * 32},),
            ]:
                write(*records)
                with self.assertRaises(ValueError):
                    report.reconcile(root, plan, phases)
            write(observed)
            with self.assertRaises(ValueError):
                report.reconcile(root, plan | {"mirrored": False}, phases)


class CleanupTests(unittest.TestCase):
    def test_image_recording_rejects_foreign_labels(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            response = json.dumps([{"Id": "image-a", "Config": {"Labels": {}}}])
            with (
                patch.object(lab, "STATE", state),
                patch.object(lab, "call", return_value=response),
            ):
                with self.assertRaises(RuntimeError):
                    lab.record_images(lab.PRODUCER)
            self.assertFalse((state / "image-ownership.json").exists())

    def test_changed_image_blocks_cleanup_before_deletion(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            config = state / "kubeconfig"
            config.write_text("private configuration")
            (state / "replication-owner.json").write_text(json.dumps({"owner": "test"}))
            (state / "image-ownership.json").write_text(
                json.dumps({lab.PRODUCER: "expected"})
            )
            response = json.dumps(
                [
                    {
                        "Id": "changed",
                        "Config": {"Labels": {"lab.example.system": lab.CLUSTER}},
                    }
                ]
            )
            with (
                patch.object(lab, "STATE", state),
                patch.object(lab, "CONFIG", config),
                patch.object(lab, "owner"),
                patch.object(lab, "kube", return_value={"items": []}),
                patch.object(lab, "call", return_value=response) as command,
            ):
                with self.assertRaises(RuntimeError):
                    lab.kind_down()
            self.assertTrue(config.exists())
            self.assertEqual(command.call_count, 1)
            self.assertEqual(
                command.call_args.args[0][:3], ["docker", "image", "inspect"]
            )

    def test_cleanup_preserves_witness_and_removes_only_recorded_images(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            data = state / "production"
            data.mkdir()
            (data / "journal").write_text("preserved")
            config = state / "kubeconfig"
            config.write_text("private configuration")
            (state / "replication-owner.json").write_text(json.dumps({"owner": "test"}))
            (state / "image-ownership.json").write_text(
                json.dumps({lab.PRODUCER: "expected"})
            )

            def respond(arguments, **kwargs):
                if arguments[:3] == ["docker", "image", "inspect"]:
                    return json.dumps(
                        [
                            {
                                "Id": "expected",
                                "Config": {
                                    "Labels": {"lab.example.system": lab.CLUSTER}
                                },
                            }
                        ]
                    )
                return ""

            with (
                patch.object(lab, "STATE", state),
                patch.object(lab, "DATA", data),
                patch.object(lab, "CONFIG", config),
                patch.object(lab, "owner"),
                patch.object(lab, "kube", return_value={"items": []}),
                patch.object(lab, "call", side_effect=respond) as command,
            ):
                lab.kind_down()
            self.assertEqual((data / "archives/test/journal").read_text(), "preserved")
            self.assertTrue((data / "archives/test/manifest.json").exists())
            self.assertFalse(config.exists())
            calls = [entry.args[0] for entry in command.call_args_list]
            self.assertEqual(
                [entry for entry in calls if entry[:3] == ["docker", "image", "rm"]],
                [["docker", "image", "rm", lab.PRODUCER]],
            )


class MountOwnershipTests(unittest.TestCase):
    def test_desktop_translation_keeps_exact_bind_ownership(self):
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            data = state / "production"
            (state / "replication-owner.json").write_text(
                json.dumps(
                    {
                        "owner": "test",
                        "nodes": {lab.CLUSTER + "-control-plane": "node-id"},
                    }
                )
            )
            mount = {
                "Type": "bind",
                "Source": "/host_mnt" + str(data.resolve()),
                "Destination": "/var/lab/mirroring",
                "RW": True,
            }
            node = {"Id": "node-id", "Mounts": [mount]}
            with (
                patch.object(lab, "STATE", state),
                patch.object(lab, "DATA", data),
                patch.object(lab, "node", return_value=node),
                patch.object(lab.sys, "platform", "darwin"),
            ):
                self.assertEqual(lab.owner()["owner"], "test")
                for field, wrong in [
                    ("Source", str(data.resolve()) + "-other"),
                    ("Type", "volume"),
                    ("RW", False),
                ]:
                    old = mount[field]
                    mount[field] = wrong
                    with self.assertRaises(RuntimeError):
                        lab.owner()
                    mount[field] = old
                with patch.object(lab.sys, "platform", "linux"):
                    with self.assertRaises(RuntimeError):
                        lab.owner()


class CutoverLockTests(unittest.TestCase):
    def test_active_cutover_rejects_second_test_and_teardown(self):
        import fcntl
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            with (state / "cutover.lock").open("a") as held:
                fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for action in ("cutover", "down"):
                    with (
                        patch.object(lab, "STATE", state),
                        patch.object(control.sys, "argv", ["control", action]),
                        patch.object(control.subprocess, "run") as execute,
                        patch.object(control, "teardown") as teardown,
                    ):
                        with self.assertRaises(BlockingIOError):
                            control.main()
                        execute.assert_not_called()
                        teardown.assert_not_called()


class ResourceTests(unittest.TestCase):
    def test_missing_cpu_is_unknown_and_counters_cover_all_threads(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "resources.jsonl.gz"

            def event(timestamp, counters):
                return {
                    "time": timestamp,
                    "node": "owned-worker",
                    "stats": {"stats": counters},
                }

            def counter(identity, cpu):
                return {
                    "attributes": {
                        "id": identity,
                        "metadata": {"name": "kafka"},
                        "labels": {},
                    },
                    "cpu": {"usageCoreNanoSeconds": {"value": cpu}},
                    "memory": {
                        "rssBytes": {"value": 1024},
                        "usageBytes": {"value": 2048},
                    },
                }

            missing = {
                "attributes": {"id": "stopped", "metadata": {"name": "kafka"}},
                "writableLayer": {},
            }
            with gzip.open(path, "wt") as stream:
                for row in (
                    event(1, [counter("existing", 1_000_000_000)]),
                    event(
                        6,
                        [
                            counter("existing", 3_000_000_000),
                            counter("new", 500_000_000),
                            missing,
                        ],
                    ),
                ):
                    stream.write(json.dumps(row) + "\n")
            summary = resources.summarize(path)
            self.assertEqual(summary["observed_total_cpu_core_seconds"], 2.5)
            self.assertEqual(
                summary["samples_without_cpu_by_container"], {"stopped": 1}
            )
            self.assertEqual(
                summary["containers"]["existing"]["sampled_max_rssBytes"], 1024
            )


class RecoveryReceiptTests(unittest.TestCase):
    def test_requires_matching_owner_strict_proofs_and_unchanged_witness(self):
        import hashlib
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            data = state / "production"
            case = "a" * 32
            root = data / "cases" / case
            (root / "east").mkdir(parents=True)
            witness = root / "east" / "producer.jsonl.gz"
            witness.write_bytes(b"immutable-witness")
            (root / "failure.json").write_text('{"passed":false}')
            (state / "replication-owner.json").write_text('{"owner":"owned-cluster"}')
            proofs = {
                side: {
                    "result": {
                        "strict_verification": True,
                        "acknowledged_loss": 0,
                        "corruption": 0,
                        "observed_acknowledged": 1,
                        "acknowledged": 1,
                        "run_id": case,
                        "mirrored_offset_policy": side == "west",
                    }
                }
                for side in ("east", "west")
            }
            recovery = data / "recoveries" / "readback"
            recovery.mkdir(parents=True)
            result = {"owner": "owned-cluster", "proofs": {case: proofs}}
            receipt = {
                "original_status": "failed",
                "recovery": "recoveries/readback",
                "proofs": proofs,
                "producer_witness_sha256": hashlib.sha256(
                    witness.read_bytes()
                ).hexdigest(),
            }
            (recovery / "result.json").write_text(json.dumps(result))
            (root / "post-crash-reconciliation.json").write_text(json.dumps(receipt))
            with patch.object(lab, "DATA", data), patch.object(lab, "STATE", state):
                self.assertTrue(control.recovered_failure(root))
                witness.write_bytes(b"changed")
                with self.assertRaises(RuntimeError):
                    control.recovered_failure(root)
                witness.write_bytes(b"immutable-witness")
                result["owner"] = "different-cluster"
                (recovery / "result.json").write_text(json.dumps(result))
                with self.assertRaises(RuntimeError):
                    control.recovered_failure(root)


if __name__ == "__main__":
    unittest.main()
