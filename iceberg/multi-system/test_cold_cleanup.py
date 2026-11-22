"""Cleanup never infers fencing from an error or removes retained database storage."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

import cold_cleanup as subject


def record(number=1, exit_code=0):
    return {
        "id": f"{number:064x}",
        "image_id": "sha256:" + "a" * 64,
        "restart_count": 0,
        "state": {
            "Running": False,
            "Pid": 0,
            "ExitCode": exit_code,
            "StartedAt": "2026-11-22T00:00:01Z",
            "FinishedAt": "2026-11-22T00:00:02Z",
            "OOMKilled": False,
            "Error": "",
        },
    }


class CleanupSafety(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        root_patch = patch.object(subject.control, "ROOT", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.checkpoint_id, self.run_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.checkpoint = self.root / "results/cold-storage" / self.checkpoint_id
        self.path = self.checkpoint / "native-readback" / self.run_id
        self.path.mkdir(parents=True)
        self.project = "iceberg-cold-readback-" + uuid.UUID(self.run_id).hex
        self.restored = {
            "Name": "iceberg-cold-" + uuid.UUID(self.checkpoint_id).hex,
            "CreatedAt": "2026-11-22T00:00:00Z",
            "Mountpoint": "/volume/restore",
            "Driver": "local",
            "Labels": {},
        }
        self.checkpoint_invocation = {
            "source_volume": {"Name": "original"},
            "services": {
                r: {"image_id": record()["image_id"]} for r in ("storage", "clickhouse")
            },
        }
        self.write(
            "invocation.json",
            {
                "project": self.project,
                "checkpoint": str(self.checkpoint.relative_to(self.root)),
                "restore_volume": self.restored,
            },
        )
        case_id, verification_id = str(uuid.uuid4()), str(uuid.uuid4())
        directory = (
            self.path / ("recovery-" + case_id) / "verifications" / verification_id
        )
        directory.mkdir(parents=True)
        exports = {}
        for engine in ("duckdb", "clickhouse"):
            file = directory / (engine + "-rows.csv")
            file.write_text("complete witnessed bodies\n")
            exports[engine] = {
                "csv_file": file.name,
                "csv_sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
            }
        self.proof = {
            "run_id": case_id,
            "verification_id": verification_id,
            "integrity_passed": True,
            "diagnostic": True,
            "passed": False,
            "exports": exports,
        }
        (directory / "verified.json").write_text(json.dumps(self.proof))
        self.write(
            "result.json",
            {
                "passed": True,
                "native_restore_verified": True,
                "error": None,
                "cleanup_errors": [],
                "verification": self.proof,
            },
        )
        for name in ("resource-summary.json", "built-source.json"):
            self.write(name, {})
        for name in ("resources.jsonl", "verifier-console.log"):
            (self.path / name).write_text("preserved\n")
        self.write(
            "compose.json",
            {
                "name": self.project,
                "services": {
                    r: {"image": record()["image_id"]}
                    for r in ("storage", "clickhouse")
                },
            },
        )
        self.write(
            "planned-verifier.json",
            {
                "name": self.project + "-verifier",
                "project": self.project,
                "role": "verifier",
                "image": record()["image_id"],
            },
        )
        for index, role in enumerate(("storage", "clickhouse", "verifier"), 1):
            final = record(index, 1 if role == "verifier" else 0)
            self.write(
                role + ("-final.json" if role == "verifier" else "-stopped.json"), final
            )
            self.write(
                role + ("-ended.json" if role == "verifier" else "-started.json"), final
            )
        checkpoint_patch = patch.object(
            subject.readback,
            "checkpoint",
            return_value=(self.checkpoint, self.checkpoint_invocation, self.restored),
        )
        checkpoint_patch.start()
        self.addCleanup(checkpoint_patch.stop)

    def write(self, name, data, path=None):
        ((path or self.path) / name).write_text(json.dumps(data))

    def selected(self):
        return subject.plan(self.path, "readback")

    def raw(self, selected, role="verifier"):
        spec = next(s for s in selected["specs"] if s["role"] == role)
        final = spec["final"]
        cpus, memory = subject.control.CAPS[
            "recovery-tools" if role == "verifier" else role
        ]
        return {
            "Id": final["id"],
            "Image": final["image_id"],
            "State": final["state"].copy(),
            "RestartCount": 0,
            "Name": "/" + spec["name"],
            "NetworkSettings": {"Networks": {selected["network"]: {}}},
            "Config": {
                "Labels": {
                    subject.PROJECT_LABEL: self.project,
                    "com.docker.compose.service": role,
                    "com.docker.compose.project.working_dir": str(self.root),
                }
            },
            "HostConfig": {
                "NetworkMode": selected["network"],
                "NanoCpus": cpus * 10**9,
                "Memory": memory,
                "MemorySwap": memory,
            },
            "Mounts": [
                {
                    "Type": kind,
                    "Name" if kind == "volume" else "Source": source,
                    "Destination": destination,
                    "RW": rw,
                }
                for kind, source, destination, rw in spec["mounts"]
            ],
        }

    def test_diagnostic_data_readback_keeps_original_failed_verdict(self):
        selected = self.selected()
        self.assertEqual(len(selected["specs"]), 3)
        self.assertFalse(self.proof["passed"])
        self.assertEqual(selected["retained"], ["original", self.restored["Name"]])

    def test_false_success_project_or_missing_evidence_blocks_plan(self):
        for name, key, value in (
            ("result.json", "native_restore_verified", False),
            ("result.json", "passed", False),
            ("invocation.json", "project", "foreign"),
        ):
            file = self.path / name
            original = file.read_bytes()
            data = json.loads(original)
            data[key] = value
            self.write(name, data)
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.selected()
            file.write_bytes(original)
        (self.path / "verifier-console.log").unlink()
        with self.assertRaises(RuntimeError):
            self.selected()

    def test_changed_csv_or_stopped_identity_is_rejected(self):
        file = next(self.path.glob("recovery-*/verifications/*/duckdb-rows.csv"))
        original = file.read_bytes()
        file.write_bytes(b"wrong bodies")
        with self.assertRaises(RuntimeError):
            self.selected()
        file.write_bytes(original)
        changed = record(99)
        self.write("storage-stopped.json", changed)
        with self.assertRaises(RuntimeError):
            self.selected()

    def test_symlinked_or_foreign_results_path_is_rejected(self):
        alias = self.path.parent / "alias"
        alias.symlink_to(self.path, target_is_directory=True)
        for path in (alias, self.root / "outside"):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                subject.owned(path)

    def test_exact_runtime_labels_image_start_caps_and_mounts_required(self):
        selected = self.selected()
        spec = selected["specs"][0]
        raw = self.raw(selected)
        subject.validate_container(raw, spec, selected)
        mutations = [
            lambda r: r["State"].update(Running=True),
            lambda r: r["State"].update(Pid=77),
            lambda r: r.update(Image="sha256:" + "b" * 64),
            lambda r: r["State"].update(StartedAt="2026-11-22T00:00:03Z"),
            lambda r: r["Config"]["Labels"].update({subject.PROJECT_LABEL: "foreign"}),
            lambda r: r["Config"]["Labels"].update(
                {"com.docker.compose.project.working_dir": "/foreign"}
            ),
            lambda r: r["Config"]["Labels"].pop(
                "com.docker.compose.project.working_dir"
            ),
            lambda r: r["Mounts"][0].update(Source="/foreign"),
            lambda r: r["HostConfig"].update(Memory=1),
        ]
        for mutate in mutations:
            changed = copy.deepcopy(raw)
            mutate(changed)
            with self.subTest(mutate=mutate), self.assertRaises(RuntimeError):
                subject.validate_container(changed, spec, selected)

    def test_unknown_container_and_failed_inspect_never_mean_absence(self):
        selected = self.selected()
        identifier = selected["specs"][0]["final"]["id"]
        with (
            patch.object(subject, "listing", side_effect=[{identifier}, {"f" * 64}]),
            self.assertRaises(RuntimeError),
        ):
            subject.preflight(selected)
        with (
            patch.object(subject, "listing", side_effect=[{identifier}, {identifier}]),
            patch.object(
                subject.cold, "inspect", side_effect=RuntimeError("transport failed")
            ),
            self.assertRaisesRegex(RuntimeError, "transport failed"),
        ):
            subject.preflight(selected)

    def test_unknown_project_volume_or_network_is_rejected(self):
        selected = self.selected()
        for volume, networks in (({"foreign"}, set()), (set(), {"one", "two"})):
            with (
                patch.object(
                    subject,
                    "listing",
                    side_effect=[
                        set(),
                        set(),
                        {self.restored["Name"]},
                        volume,
                        networks,
                    ],
                ),
                patch.object(subject, "inspect_resource", return_value=self.restored),
                self.subTest(volume=volume),
                self.assertRaisesRegex(RuntimeError, "unregistered project"),
            ):
                subject.preflight(selected)

    def test_healthy_native_absence_and_retained_restore_are_required(self):
        selected = self.selected()
        with (
            patch.object(
                subject,
                "listing",
                side_effect=[set(), set(), {self.restored["Name"]}, set(), set()],
            ),
            patch.object(subject, "inspect_resource", return_value=self.restored),
            patch.object(subject.control, "call", return_value=""),
        ):
            observed = subject.preflight(selected)
        self.assertFalse(observed["containers"])
        self.assertEqual(observed["retained"], {self.restored["Name"]: self.restored})
        with (
            patch.object(subject, "listing", side_effect=[set(), set(), set()]),
            self.assertRaisesRegex(RuntimeError, "restore volume is absent"),
        ):
            subject.preflight(selected)

    def test_foreign_query_volume_user_blocks_removal(self):
        selected = self.selected()
        query = {
            "Name": selected["query"],
            "Labels": {
                subject.PROJECT_LABEL: self.project,
                "com.docker.compose.volume": "query",
            },
            "Driver": "local",
            "Options": None,
        }
        with (
            patch.object(
                subject,
                "listing",
                side_effect=[
                    set(),
                    set(),
                    {self.restored["Name"], selected["query"]},
                    {selected["query"]},
                    set(),
                    {"foreign-user"},
                ],
            ),
            patch.object(
                subject, "inspect_resource", side_effect=[self.restored, query]
            ),
            self.assertRaisesRegex(RuntimeError, "unregistered query-volume user"),
        ):
            subject.preflight(selected)

    def test_recreated_retained_volume_or_inconsistent_listing_blocks_cleanup(self):
        selected = self.selected()
        changed = self.restored | {"CreatedAt": "2026-11-22T01:00:00Z"}
        with (
            patch.object(
                subject, "listing", side_effect=[set(), set(), {self.restored["Name"]}]
            ),
            patch.object(subject, "inspect_resource", return_value=changed),
            self.assertRaisesRegex(RuntimeError, "volume identity"),
        ):
            subject.preflight(selected)
        identifier = selected["specs"][0]["final"]["id"]
        with (
            patch.object(subject, "listing", side_effect=[set(), {identifier}]),
            self.assertRaisesRegex(RuntimeError, "unregistered containers"),
        ):
            subject.preflight(selected)

    def test_only_exact_temporary_ids_query_volume_and_network_are_removed(self):
        selected = self.selected()
        raws = {
            s["final"]["id"]: self.raw(selected, s["role"]) for s in selected["specs"]
        }
        before = {
            "containers": raws,
            "network": {"Id": "private-network"},
            "query": {"Name": selected["query"]},
            "retained": {"original": {"Name": "original"}},
        }
        after = before | {"containers": {}, "network": None, "query": None}
        with (
            patch.object(subject.control, "local_docker"),
            patch.object(subject, "plan", return_value=selected),
            patch.object(subject, "preflight", side_effect=[before, after]),
            patch.object(subject.cold, "inspect", side_effect=raws.get),
            patch.object(subject.control, "call") as call,
        ):
            receipt = subject.run(self.path, "readback")
        calls = [c.args[0] for c in call.call_args_list]
        self.assertEqual(
            calls[-2:],
            [
                ["docker", "network", "rm", "private-network"],
                ["docker", "volume", "rm", selected["query"]],
            ],
        )
        self.assertTrue(all("--force" not in c and "-v" not in c for c in calls))
        self.assertTrue(json.loads((receipt / "result.json").read_text())["passed"])

    def test_repeat_cleanup_uses_healthy_absence_and_makes_no_mutations(self):
        empty = {"containers": {}, "network": None, "query": None, "retained": {}}
        with (
            patch.object(subject.control, "local_docker"),
            patch.object(subject, "preflight", return_value=empty),
            patch.object(subject.control, "call") as call,
        ):
            first = subject.run(self.path, "readback")
            second = subject.run(self.path, "readback")
        call.assert_not_called()
        self.assertNotEqual(first, second)

    def test_failed_cleanup_preserves_nonzero_verdict_and_receipt(self):
        with (
            patch.object(subject.control, "local_docker"),
            patch.object(
                subject, "preflight", side_effect=RuntimeError("native listing failed")
            ),
            self.assertRaisesRegex(RuntimeError, "native listing failed"),
        ):
            subject.run(self.path, "readback")
        receipt = next((self.path / "cleanup").glob("*/result.json"))
        self.assertFalse(json.loads(receipt.read_text())["passed"])

    def test_evidence_change_after_preflight_blocks_first_removal(self):
        selected = self.selected()

        def observation(_):
            (self.path / "verifier-console.log").write_text("changed after plan\n")
            return {"containers": {}, "network": None, "query": None, "retained": {}}

        with (
            patch.object(subject.control, "local_docker"),
            patch.object(subject, "plan", return_value=selected),
            patch.object(subject, "preflight", side_effect=observation),
            patch.object(subject.control, "call") as call,
            self.assertRaisesRegex(RuntimeError, "before cleanup"),
        ):
            subject.run(self.path, "readback")
        call.assert_not_called()

    def checkpoint_fixture(self, legacy=False):
        path = (
            self.checkpoint
            if not legacy
            else self.checkpoint.parent / subject.LEGACY_PERMISSION_CASE
        )
        path.mkdir(exist_ok=True)
        run_id = path.name
        self.write(
            "invocation.json",
            {
                "run_id": run_id,
                "restore_volume": "iceberg-cold-" + uuid.UUID(run_id).hex,
                "source_volume": {"Name": "original"},
                "utility_image": record()["image_id"],
                "source_sha256": {"cold_storage.py": subject.LEGACY_SOURCE},
            },
            path,
        )
        self.write("result.json", {"run_id": run_id, "passed": not legacy}, path)
        final = record(7, 123 if legacy else 0)
        self.write("backup-created.json", final, path)
        self.write("backup-final.json", final, path)
        self.write("backup-resources.json", {}, path)
        (path / "backup-console.log").write_text("original failed permissions\n")
        return path

    def test_failed_permission_checkpoint_is_narrowly_identified(self):
        path = self.checkpoint_fixture(legacy=True)
        selected = subject.plan(path, "checkpoint")
        self.assertTrue(selected["specs"][0]["legacy"])
        self.assertEqual(selected["specs"][0]["final"]["state"]["ExitCode"], 123)
        invocation = json.loads((path / "invocation.json").read_text())
        invocation["source_sha256"]["cold_storage.py"] = "changed"
        self.write("invocation.json", invocation, path)
        self.assertFalse(subject.plan(path, "checkpoint")["specs"][0]["legacy"])

    def test_checkpoint_final_logs_and_resources_are_mandatory(self):
        path = self.checkpoint_fixture()
        subject.plan(path, "checkpoint")
        for name in (
            "backup-final.json",
            "backup-console.log",
            "backup-resources.json",
        ):
            file = path / name
            data = file.read_bytes()
            file.unlink()
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                subject.plan(path, "checkpoint")
            file.write_bytes(data)

    def test_checkpoint_capabilities_accept_native_prefix_but_no_extra_power(self):
        path = self.checkpoint_fixture()
        selected = subject.plan(path, "checkpoint")
        spec = selected["specs"][0]
        final = spec["final"]
        raw = {
            "Id": final["id"],
            "Image": final["image_id"],
            "RestartCount": 0,
            "State": final["state"].copy(),
            "Name": "/" + spec["name"],
            "Config": {"Labels": {subject.cold.LABEL: path.name}, "User": "0:0"},
            "HostConfig": {
                "NetworkMode": "none",
                "NanoCpus": 10**9,
                "Memory": 512 * 1024**2,
                "MemorySwap": 512 * 1024**2,
                "ReadonlyRootfs": True,
                "CapDrop": ["CAP_ALL"],
                "CapAdd": ["CAP_DAC_OVERRIDE"],
                "SecurityOpt": ["no-new-privileges:true"],
            },
            "Mounts": [
                {
                    "Type": kind,
                    "Name" if kind == "volume" else "Source": source,
                    "Destination": destination,
                    "RW": rw,
                }
                for kind, source, destination, rw in spec["mounts"]
            ],
        }
        subject.validate_container(raw, spec, selected)
        for changes in (
            {"CapAdd": []},
            {"CapAdd": ["CAP_DAC_OVERRIDE", "CAP_SYS_ADMIN"]},
            {"NetworkMode": "bridge"},
            {"MemorySwap": -1},
        ):
            changed = copy.deepcopy(raw)
            changed["HostConfig"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                subject.validate_container(changed, spec, selected)
        legacy_path = self.checkpoint_fixture(legacy=True)
        legacy = subject.plan(legacy_path, "checkpoint")
        legacy_spec = legacy["specs"][0]
        raw["Name"] = "/" + legacy_spec["name"]
        raw["State"] = legacy_spec["final"]["state"].copy()
        raw["Config"]["Labels"] = {subject.cold.LABEL: legacy_path.name}
        raw["HostConfig"]["CapAdd"] = []
        raw["Mounts"][0]["Source"] = str(legacy_path)
        subject.validate_container(raw, legacy_spec, legacy)
        with self.assertRaises(RuntimeError):
            subject.validate_container(raw, legacy_spec | {"legacy": False}, legacy)


if __name__ == "__main__":
    unittest.main()
