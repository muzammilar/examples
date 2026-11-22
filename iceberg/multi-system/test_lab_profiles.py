"""Each fixed lane must own disjoint resources and history before Docker runs."""

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path

import lab_profiles as lab
import recovery_control as control
import recovery_cleanup as cleanup


class LabIsolation(unittest.TestCase):
    def inspect(self, profile, version="2.0.1"):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import json,recovery_control as c,recovery_cleanup as k; print(json.dumps({'project':c.PROJECT,'owner':c.OWNER,'compose':c.COMPOSE,'results':str(k.results_root().relative_to(c.ROOT)),'images':k.IMAGES,'versions':c.lab.MAINTENANCE,'runner':c.lab.GO_IMAGE}))",
            ],
            env=(
                {k: v for k, v in os.environ.items() if k != "LAB_PROFILE"}
                | {"DUCKDB_VERSION": version}
                | ({"LAB_PROFILE": profile} if profile is not None else {})
            ),
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_current_is_disjoint_from_preserved_historical_resources(self):
        old, new = [self.inspect(p) for p in ("historical", "current")]
        self.assertEqual(old.returncode, 0, old.stderr)
        self.assertEqual(new.returncode, 0, new.stderr)
        old, new = json.loads(old.stdout), json.loads(new.stdout)
        self.assertEqual(old["project"], "iceberg-multi-system")
        old_index = old["compose"].index("--project-name")
        self.assertEqual(old["compose"][old_index + 1], old["project"])
        self.assertEqual(new["project"], "iceberg-multi-system-current")
        self.assertEqual(new["results"], "results/current")
        self.assertEqual(old["results"], "results")
        self.assertEqual(old["owner"], new["owner"])
        self.assertFalse(set(old["images"]) & set(new["images"]))
        self.assertIn("--project-name", new["compose"])
        index = new["compose"].index("--project-name")
        self.assertEqual(new["compose"][index + 1], new["project"])
        self.assertIn("docker-compose.current.yml", new["compose"])
        self.assertEqual(
            new["versions"], {"spark_version": "4.1.4", "iceberg_version": "1.12.0"}
        )
        self.assertIn(new["runner"], new["images"])

    def test_default_selects_exact_current_namespace_and_release(self):
        default, explicit = self.inspect(None), self.inspect("current")
        self.assertEqual(default.returncode, 0, default.stderr)
        self.assertEqual(json.loads(default.stdout), json.loads(explicit.stdout))

    def test_arbitrary_project_and_old_engine_are_rejected_before_runtime(self):
        for profile, version in [("production", "2.0.1"), ("current", "1.5.5")]:
            result = self.inspect(profile, version)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(
                "must be" if profile == "production" else "requires", result.stderr
            )


class MakeNamespaceGuard(unittest.TestCase):
    def test_direct_workload_commands_reject_other_lane_before_docker(self):
        with tempfile.TemporaryDirectory() as temporary:
            fake = Path(temporary)
            marker = fake / "docker-called"
            docker = fake / "docker"
            docker.write_text(
                '#!/bin/sh\necho called > "$LAB_TEST_DOCKER_MARKER"\nexit 97\n'
            )
            docker.chmod(0o700)
            env = os.environ | {
                "PATH": str(fake) + ":" + os.environ["PATH"],
                "LAB_TEST_DOCKER_MARKER": str(marker),
            }
            for target in ("probe", "load", "verify", "compact"):
                with self.subTest(target=target):
                    run = subprocess.run(
                        [
                            "make",
                            "--no-print-directory",
                            target,
                            "LAB_PROFILE=current",
                            "RESULTS_DIR=results/manual",
                        ],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    self.assertNotEqual(run.returncode, 0)
                    self.assertIn("namespace", run.stderr)
                    self.assertFalse(marker.exists())


class NamespaceBoundaries(unittest.TestCase):
    def test_historical_cannot_register_in_current_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            with self.assertRaisesRegex(RuntimeError, "namespace"):
                lab.validate_results(root / "results/current/other", root)
            self.assertEqual(
                lab.validate_results("results/manual", root), root / "results/manual"
            )

    def test_each_lane_detects_unregistered_cases_only_in_its_namespace(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            unregistered = root / "results/current/manual/production/fake"
            unregistered.mkdir(parents=True)
            with patch.object(control, "ROOT", root):
                self.assertEqual(cleanup.cases_by_volume(), {})
                with (
                    patch.object(lab, "RESULTS_SUBDIR", "results/current"),
                    patch.object(lab, "PROFILE", "current"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "unregistered"):
                        cleanup.cases_by_volume()


class EffectiveSettings(unittest.TestCase):
    def test_native_memory_and_capacity_are_checked(self):
        storage = {
            "Id": "storage",
            "Config": {
                "Cmd": [
                    "mini",
                    "-volume.max=16",
                    "-master.volumeSizeLimitMB=256",
                    "-s3.readerCacheSizeMB=256",
                    "-s3.cacheCapacityMB=0",
                ]
            },
        }
        for memory, passes in [("629145600", True), ("1000000000", False)]:
            outputs = [
                "version 4.54 5ef99c1d8 linux arm64",
                "26.10.10.3",
                "go_gc_gomemlimit_bytes " + memory + "\n",
                '{"Topology":{"Max":16}}',
            ]
            with (
                self.subTest(memory=memory),
                patch.object(control, "call", side_effect=outputs),
            ):
                if passes:
                    self.assertEqual(
                        control.current_settings(storage, {"Id": "query"})[
                            "native_volume_slots"
                        ],
                        16,
                    )
                else:
                    with self.assertRaisesRegex(RuntimeError, "settings differ"):
                        control.current_settings(storage, {"Id": "query"})


if __name__ == "__main__":
    unittest.main()
