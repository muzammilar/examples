"""Filesystem/decision controls; native engine probes run only through Make."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from unittest.mock import Mock
import uuid

import manifest_control as manifest

RUN = "5742e029-c49e-4e57-8832-ddc6495e1e8d"
TABLE_UUID = "c530feb6-32f9-4738-89bc-df2e8683c852"
PREFIX = f"/warehouse/lab/recovery_{uuid.UUID(RUN).hex}-{TABLE_UUID}/metadata/"
STATE = {"Running": False, "Pid": 0, "OOMKilled": False, "ExitCode": 1}
REQUESTS = [
    {
        "action": action,
        "method": "GET",
        "request_uri": PREFIX + "manifest-m0.avro",
        "signed_host": "127.0.0.1:9999",
        "ns": number,
    }
    for number, action in enumerate(
        ("manifest-connection-close-intent", "manifest-connection-closed"), 1
    )
]
ERROR = {
    "passed": False,
    "dropped_requests": 1,
    "error": "Server returned nothing: " + PREFIX + "manifest-m0.avro",
}


class Outcomes(unittest.TestCase):
    def assess(
        self,
        state=None,
        report=None,
        requests=None,
        stderr="",
        mode="drop-data-manifest",
    ):
        return manifest.outcome(
            mode,
            STATE if state is None else state,
            ERROR if report is None else report,
            REQUESTS if requests is None else requests,
            stderr,
            10000,
            PREFIX,
        )

    def test_handled_error_passes_but_native_query_did_not(self):
        result = self.assess()
        self.assertTrue(result["passed"])
        self.assertFalse(result["native_query_passed"])
        self.assertTrue(result["expected_sql_error"])

    def test_native_abort_remains_a_failed_backend_result(self):
        result = manifest.outcome(
            "drop-data-manifest",
            STATE | {"ExitCode": 2},
            None,
            REQUESTS,
            "SIGABRT: Server returned nothing",
            10000,
            PREFIX,
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["backend_contract_passed"])
        self.assertTrue(result["reproduced_native_abort"])

    def test_success_wrong_error_and_unknown_process_are_rejected(self):
        for changes in (
            {"ExitCode": 0},
            {"Running": True},
            {"Pid": 22},
            {"OOMKilled": True},
            {"ExitCode": 137},
        ):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.assess(state=STATE | changes)
        with self.assertRaises(RuntimeError):
            self.assess(report=ERROR | {"error": "unrelated timeout"})

    def test_fault_witness_cannot_be_absent_foreign_unpaired_or_reversed(self):
        variants = [[], REQUESTS[1:]]
        for key, value in (
            ("method", "HEAD"),
            ("request_uri", "/foreign/m.avro"),
            ("request_uri", PREFIX + "snap-1.avro"),
            ("ns", 0),
            ("signed_host", "different"),
            ("error", "close failed"),
        ):
            changed = copy.deepcopy(REQUESTS)
            changed[1][key] = value
            variants.append(changed)
        for requests in variants:
            with self.subTest(requests=requests), self.assertRaises(RuntimeError):
                self.assess(requests=requests)

    def test_healthy_count_and_checksum_are_both_required(self):
        report = {
            "passed": True,
            "dropped_requests": 0,
            "rows": 10000,
            "amount_sum": 50005000,
        }
        self.assess(
            state=STATE | {"ExitCode": 0}, report=report, requests=[], mode="healthy"
        )
        for changes in (
            {"rows": 9999},
            {"amount_sum": 50004999},
            {"dropped_requests": 1},
            {"passed": False},
        ):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.assess(
                    state=STATE | {"ExitCode": 0},
                    report=report | changes,
                    requests=[],
                    mode="healthy",
                )


class SourceFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.case = self.root / "results" / "production" / RUN
        self.workload = self.case / ("recovery-" + RUN)
        self.inspection = str(uuid.uuid4())
        self.directory = self.workload / "observations" / self.inspection
        self.directory.mkdir(parents=True)
        self.config = {
            "version": 1,
            "run_id": RUN,
            "table": "recovery_" + uuid.UUID(RUN).hex,
            "seed_rows": 10000,
            "batch_rows": 10000,
        }
        self.write(self.workload / "config.json", self.config)
        self.metadata = {
            "volume": {"Name": "owned", "CreatedAt": "created", "Mountpoint": "/owned"}
        }
        self.write(self.case / "case.json", self.metadata)
        snapshot = {
            "table_uuid": TABLE_UUID,
            "snapshot_id": 9007199254740993,
            "metadata_location": "s3://warehouse"
            + PREFIX.removeprefix("/warehouse")
            + "v1.metadata.json",
        }
        self.proof = {
            "passed": True,
            "run_id": RUN,
            "config_sha256": manifest.cleanup.digest(self.workload / "config.json"),
            "phase": "before",
            "observation_id": self.inspection,
            "catalog": snapshot,
            "pin": {
                "observation_id": self.inspection,
                "snapshot": snapshot,
                "proof": {"rows": 10000, "seed_rows": 10000, "body_sha256": "a" * 64},
            },
        }
        self.write(self.directory / "inspection.json", self.proof)
        self.write(
            self.directory / "catalog.json",
            {
                "available": True,
                "run_id": RUN,
                "config_sha256": self.proof["config_sha256"],
                "load_table": {
                    "metadata-location": snapshot["metadata_location"],
                    "metadata": {
                        "table-uuid": TABLE_UUID,
                        "current-snapshot-id": snapshot["snapshot_id"],
                        "snapshots": [{"snapshot-id": snapshot["snapshot_id"]}],
                    },
                },
            },
        )
        for patcher in (
            patch.object(manifest.control, "ROOT", self.root),
            patch.object(
                manifest.cleanup,
                "cases_by_volume",
                return_value={"owned": [(self.case, self.metadata)]},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, value):
        path.write_text(json.dumps(value) + "\n")

    def test_exact_registered_inspection_preserves_int64_pin(self):
        selected = manifest.source_fixture(self.case, inspection=self.inspection)
        self.assertEqual(selected[3]["snapshot_id"], 9007199254740993)
        self.assertEqual(selected[4], 10000)

    def test_outside_traversal_and_symlink_cases_are_rejected(self):
        linked = self.root / "results" / "linked"
        linked.symlink_to(self.case, target_is_directory=True)
        for path in (self.root, self.case / ".." / RUN, linked):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                manifest.source_fixture(path, inspection=self.inspection)

    def test_failed_or_cross_config_inspection_is_not_a_data_fixture(self):
        for changes in ({"passed": False}, {"config_sha256": "b" * 64}):
            self.write(self.directory / "inspection.json", self.proof | changes)
            with self.assertRaises(RuntimeError):
                manifest.source_fixture(self.case, inspection=self.inspection)

    def test_default_failed_case_cannot_select_a_diagnostic_verification(self):
        self.write(
            self.case / "result.json",
            {"passed": False, "verification_id": str(uuid.uuid4())},
        )
        with self.assertRaisesRegex(
            RuntimeError, "default source result must be successful"
        ):
            manifest.source_fixture(self.case)

    def test_native_volume_replacement_is_rejected_before_launch(self):
        live = {
            "Id": "storage",
            "State": {
                "Running": True,
                "OOMKilled": False,
                "Health": {"Status": "healthy"},
            },
        }
        volume = self.metadata["volume"] | {
            "Labels": {"com.docker.compose.project": manifest.control.PROJECT}
        }
        with (
            patch.object(manifest.control, "service", return_value=live),
            patch.object(manifest.control, "data_volume", return_value="owned"),
            patch.object(
                manifest.control,
                "call",
                return_value=json.dumps([volume | {"CreatedAt": "replaced"}]),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "volume differs"):
                manifest.active_fixture(self.metadata)

    def test_backend_abort_is_saved_and_controller_exits_nonzero(self):
        original = {
            str(p.relative_to(self.case)): p.read_bytes()
            for p in self.case.rglob("*")
            if p.is_file()
        }
        source = manifest.source_fixture(self.case, inspection=self.inspection)
        sampler = Mock()
        sampler.close.return_value = ([], {"available": True})
        with (
            patch.object(manifest.control, "local_docker"),
            patch.object(manifest, "source_fixture", return_value=source),
            patch.object(
                manifest,
                "active_fixture",
                return_value=({"Id": "storage"}, {"Id": "query"}),
            ),
            patch.object(
                manifest.control, "profile", return_value={"network_ids": set()}
            ),
            patch.object(manifest.control, "sources", return_value={}),
            patch.object(manifest, "ResourceSampler", return_value=sampler),
            patch.object(
                manifest,
                "run_probe",
                side_effect=[
                    {"passed": True},
                    {"passed": False, "reproduced_native_abort": True},
                ],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "native abort"):
                manifest.run(self.case, inspection=self.inspection)
        result = next((self.root / "results" / "manifest-io").glob("*/*/result.json"))
        saved = json.loads(result.read_text())
        self.assertFalse(saved["passed"])
        self.assertTrue(saved["reproduced_native_abort"])
        self.assertEqual(
            original,
            {
                str(p.relative_to(self.case)): p.read_bytes()
                for p in self.case.rglob("*")
                if p.is_file()
            },
        )


if __name__ == "__main__":
    unittest.main()
