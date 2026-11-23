import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import enrichment_lab as lab
import enrichment_lifecycle as lifecycle


class LifecycleTests(unittest.TestCase):
    def test_leftover_owned_network_blocks_closed_fixture(self):
        with self.assertRaisesRegex(RuntimeError, "resources remain"):
            lifecycle.assert_absent(
                {
                    "containers": [],
                    "volumes": [],
                    "networks": [
                        {"Labels": {"com.docker.compose.project": lab.PROJECT}}
                    ],
                }
            )

    def test_jvm_sigterm_requires_native_completion_and_never_accepts_sigkill(self):
        row = self.row(role="connect-1")
        row["State"].update(OOMKilled=False, ExitCode=143)
        self.assertTrue(lifecycle.clean_shutdown(row, "Kafka Connect stopped"))
        self.assertFalse(lifecycle.clean_shutdown(row, "shutdown started"))
        row["State"]["ExitCode"] = 137
        self.assertFalse(lifecycle.clean_shutdown(row, "Kafka Connect stopped"))
        row["State"].update(ExitCode=143, OOMKilled=True)
        self.assertFalse(lifecycle.clean_shutdown(row, "Kafka Connect stopped"))

    def test_kraft_shutdown_checks_the_actual_role_and_node(self):
        for role, marker in (
            ("kafka-broker-2", "[BrokerServer id=5] shut down completed"),
            ("kafka-controller-2", "App info kafka.server for 2 unregistered"),
        ):
            with self.subTest(role=role):
                row = self.row(role=role)
                row["State"].update(OOMKilled=False, ExitCode=143)
                self.assertTrue(lifecycle.clean_shutdown(row, marker))
                self.assertFalse(lifecycle.clean_shutdown(row, "shutting down"))
                self.assertFalse(
                    lifecycle.clean_shutdown(
                        row, "[BrokerServer id=4] shut down completed"
                    )
                )

    def test_reapply_build_skips_only_matching_sources_and_image_ids(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(lab, "ROOT", Path(temp)),
            patch.object(lab, "STATE", Path(temp) / "results"),
            patch.object(lifecycle.prepare_workload, "main"),
            patch.object(lab, "compose") as compose,
        ):
            source = lab.ROOT / ".build/enrichment/producer/main.cpp"
            source.parent.mkdir(parents=True)
            source.write_text("first version")
            tags = (
                lab.PROJECT + "-processor:latest",
                "kafka-enrichment-witness-producer:dev",
                "kafka-enrichment-observer:dev",
            )

            def call(args, **_kwargs):
                if args[0] == "git":
                    return ""
                if args[1:3] == ["image", "ls"]:
                    return "\n".join(tag + " image-id" for tag in tags)
                return json.dumps([{"Id": "image-id"}])

            with patch.object(lab, "call", side_effect=call):
                lifecycle.build_workloads()
                lifecycle.build_workloads()
                self.assertEqual(compose.call_count, 1)
                source.write_text("changed version")
                lifecycle.build_workloads()
                self.assertEqual(compose.call_count, 2)

    def test_image_cleanup_refuses_unowned_tag(self):
        image = {"Id": "image-id", "Config": {"Labels": {}}}
        with patch.object(
            lab,
            "call",
            side_effect=["kafka-enrichment-observer:dev\n", json.dumps([image])],
        ) as call:
            with self.assertRaisesRegex(RuntimeError, "owned build label"):
                lifecycle.remove_workload_images()
            self.assertEqual(call.call_count, 2)

    def test_image_cleanup_refuses_foreign_container_reference(self):
        image = {
            "Id": "image-id",
            "Config": {"Labels": {"lab.example.system": lab.PROJECT}},
        }
        with (
            patch.object(
                lab,
                "call",
                side_effect=["kafka-enrichment-observer:dev\n", json.dumps([image])],
            ) as call,
            patch.object(
                lifecycle,
                "inventory",
                return_value={"containers": [{"Image": "image-id"}]},
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "still used"):
                lifecycle.remove_workload_images()
            self.assertEqual(call.call_count, 2)

    def row(self, *, root=None, volume="owned", running=False, role="processor"):
        return {
            "Id": "container",
            "Config": {
                "Labels": {
                    "com.docker.compose.project": lab.PROJECT,
                    "com.docker.compose.project.working_dir": str(root or lab.ROOT),
                    "com.docker.compose.service": role,
                }
            },
            "Mounts": [{"Type": "volume", "Name": volume}],
            "State": {"Running": running},
        }

    def test_same_project_other_checkout_is_not_adopted(self):
        with self.assertRaisesRegex(RuntimeError, "another checkout"):
            lifecycle.owned({"containers": [self.row(root="/other")]})

    def test_unreferenced_check_refuses_live_or_stopped_reference(self):
        for running in (False, True):
            with (
                self.subTest(running=running),
                patch.object(
                    lifecycle,
                    "inventory",
                    return_value={
                        "containers": [self.row(running=running)],
                        "volumes": [{"Name": "owned"}],
                    },
                ),
                patch.object(lab, "call") as call,
            ):
                with self.assertRaisesRegex(RuntimeError, "still references"):
                    lifecycle.remove_unreferenced(["owned"])
                call.assert_not_called()

    def test_runtime_failure_never_proves_volume_absence(self):
        with (
            patch.object(
                lifecycle, "inventory", side_effect=RuntimeError("transport failed")
            ),
            patch.object(lab, "call") as call,
        ):
            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                lifecycle.remove_unreferenced(["owned"])
            call.assert_not_called()

    def test_remove_only_recorded_unreferenced_volume(self):
        with (
            patch.object(
                lifecycle,
                "inventory",
                return_value={
                    "containers": [],
                    "volumes": [{"Name": "owned"}, {"Name": "foreign"}],
                },
            ),
            patch.object(lab, "call") as call,
        ):
            lifecycle.remove_unreferenced(["owned", "already-absent"])
            call.assert_called_once_with(["docker", "volume", "rm", "owned"])

    def test_live_workload_blocks_cleanup(self):
        with self.assertRaisesRegex(RuntimeError, "still running"):
            lifecycle.assert_no_live_workloads(
                [self.row(running=True, role="source-reader")]
            )

    def test_closed_history_is_archived_without_rewriting(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(lab, "STATE", Path(temp)),
        ):
            lab.save(lab.STATE / "owner.json", {"owner": "abc", "project": lab.PROJECT})
            (lab.STATE / "CLOSED.json").write_text("{}\n")
            case = lab.STATE / "cases" / "failed"
            case.mkdir(parents=True)
            failure = b'{"passed":false,"error":"original"}\n'
            (case / "failure.json").write_bytes(failure)
            (lab.STATE / "experiment.lock").touch()
            lifecycle.archive_closed({"containers": [], "volumes": []})
            self.assertEqual(
                (lab.STATE / "archives/abc/cases/failed/failure.json").read_bytes(),
                failure,
            )
            self.assertTrue((lab.STATE / "experiment.lock").exists())
            self.assertFalse((lab.STATE / "owner.json").exists())

    def test_closed_fixture_with_volume_cannot_be_archived(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(lab, "STATE", Path(temp)),
        ):
            lab.save(lab.STATE / "owner.json", {"owner": "abc", "project": lab.PROJECT})
            lab.save(lab.STATE / "resources.json", {"volumes": ["anonymous"]})
            with self.assertRaisesRegex(RuntimeError, "resources remain"):
                lifecycle.archive_closed(
                    {"containers": [], "volumes": [{"Name": "anonymous"}]}
                )
            self.assertTrue((lab.STATE / "owner.json").exists())

    def test_reapply_keeps_transactional_identity_and_topics(self):
        row = self.row()
        row["Config"]["Env"] = [
            "INPUT_TOPIC=prior",
            "OUTPUT_TOPIC=prior.out",
            "CONSUMER_GROUP=prior-group",
            "TRANSACTIONAL_ID=prior-txn",
        ]
        with (
            patch.object(lab, "compose", return_value="container\n"),
            patch.object(lab, "inspect", return_value=row),
        ):
            self.assertEqual(
                lifecycle.processor_environment(),
                {
                    "INPUT_TOPIC": "prior",
                    "OUTPUT_TOPIC": "prior.out",
                    "PROCESSOR_GROUP": "prior-group",
                    "TRANSACTIONAL_ID": "prior-txn",
                },
            )

    def test_removed_processor_uses_persisted_identity(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(lab, "STATE", Path(temp)),
            patch.object(lab, "compose", return_value=""),
        ):
            saved = {
                "INPUT_TOPIC": "retained",
                "OUTPUT_TOPIC": "retained.out",
                "PROCESSOR_GROUP": "retained-group",
                "TRANSACTIONAL_ID": "retained-transaction",
            }
            lab.save(lab.STATE / "processor.json", saved)
            self.assertEqual(lifecycle.processor_environment(), saved)


if __name__ == "__main__":
    unittest.main()
