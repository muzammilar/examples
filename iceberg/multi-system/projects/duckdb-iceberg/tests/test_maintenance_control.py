"""Reject incorrect identities, mounts and isolation before native actions."""

import copy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import json

import maintenance_control as maintenance
import maintenance_smoke


class ProbeOwnership(unittest.TestCase):
    def setUp(self):
        self.root = Path("/owned/results/probe")
        self.value = {
            "Id": "a" * 64,
            "Image": "sha256:" + "b" * 64,
            "Name": "/probe",
            "Config": {"Labels": {maintenance.LABEL: "run"}},
            "HostConfig": {
                "NetworkMode": "none",
                "NanoCpus": 2000000000,
                "Memory": 2147483648,
                "MemorySwap": 2147483648,
                "ReadonlyRootfs": True,
                "Privileged": False,
                "Tmpfs": {
                    "/data": "rw,size=536870912,mode=1777",
                    "/tmp": "rw,exec,size=536870912,mode=1777",
                },
            },
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(self.root),
                    "Destination": "/results",
                    "RW": True,
                },
                {
                    "Type": "bind",
                    "Source": str(self.root / "maintenance_smoke.py"),
                    "Destination": "/opt/lab/maintenance_smoke.py",
                    "RW": False,
                },
            ],
        }

    def check(self, value):
        with patch.object(
            maintenance.control, "call", return_value=json.dumps([value])
        ):
            return maintenance.owned(
                "a" * 64, "sha256:" + "b" * 64, "probe", "run", self.root
            )

    def test_exact_probe_is_accepted(self):
        self.assertEqual(self.check(self.value), self.value)

    def test_identity_isolation_caps_and_mount_drift_are_rejected(self):
        for change in [
            lambda v: v.update(Id="foreign"),
            lambda v: v.update(Image="foreign"),
            lambda v: v["Config"]["Labels"].update(
                {"com.docker.compose.project": "foreign"}
            ),
            lambda v: v["HostConfig"].update(NetworkMode="host"),
            lambda v: v["HostConfig"].update(Memory=0),
            lambda v: v["HostConfig"].update(Privileged=True),
            lambda v: v["HostConfig"].update(ReadonlyRootfs=False),
            lambda v: v["HostConfig"]["Tmpfs"].update({"/host": "rw"}),
            lambda v: v["Mounts"][1].update(RW=True),
            lambda v: v["Mounts"][0].update(Source="/foreign"),
            lambda v: v["Mounts"].append(copy.deepcopy(v["Mounts"][0])),
        ]:
            value = copy.deepcopy(self.value)
            change(value)
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, "unproven"):
                    self.check(value)

    def test_runtime_failure_is_unknown_not_absent(self):
        with patch.object(
            maintenance.control,
            "call",
            side_effect=RuntimeError("transport unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "transport unavailable"):
                maintenance.owned(
                    "a" * 64, "sha256:" + "b" * 64, "probe", "run", self.root
                )


class ProbeFinalization(unittest.TestCase):
    def test_stop_and_sampler_errors_preserve_original_failure(self):
        original = RuntimeError("wrong body")
        spark, sampler, report = Mock(), Mock(), {}
        spark.stop.side_effect = OSError("stop failed")
        sampler.close.side_effect = OSError("sampling failed")
        saved = []
        actual = maintenance_smoke.finish(
            report,
            spark,
            sampler,
            lambda: saved.append(copy.deepcopy(report)),
            0,
            original,
        )
        self.assertIs(actual, original)
        self.assertFalse(saved[0]["passed"])
        self.assertEqual(len(saved[0]["errors"]), 2)
        self.assertIn("wrong body", saved[0]["error"])
        self.assertEqual(len(original.__notes__), 2)

    def test_shutdown_failure_cannot_publish_pass(self):
        spark, sampler, report = Mock(), Mock(), {}
        sampler.close.return_value = {"samples": 4}
        spark.stop.side_effect = RuntimeError("stop failed")
        saved = []
        error = maintenance_smoke.finish(
            report, spark, sampler, lambda: saved.append(copy.deepcopy(report)), 0, None
        )
        self.assertIs(error, spark.stop.side_effect)
        self.assertFalse(saved[0]["passed"])
        self.assertEqual(saved[0]["resources"]["samples"], 4)

    def test_success_is_published_only_after_both_closures(self):
        order, report = [], {}
        spark, sampler = Mock(), Mock()
        spark.stop.side_effect = lambda: order.append("stop")
        sampler.close.side_effect = lambda: order.append("sample") or {"samples": 4}
        error = maintenance_smoke.finish(
            report, spark, sampler, lambda: order.append("save"), 0, None
        )
        self.assertIsNone(error)
        self.assertEqual(order, ["stop", "sample", "save"])
        self.assertTrue(report["passed"])

    def test_report_failure_propagates_without_claiming_pass(self):
        spark, sampler, report = Mock(), Mock(), {}
        save_error = OSError("witness full")
        actual = maintenance_smoke.finish(
            report, spark, sampler, Mock(side_effect=save_error), 0, None
        )
        self.assertIs(actual, save_error)
        self.assertFalse(report["passed"])


if __name__ == "__main__":
    unittest.main()
