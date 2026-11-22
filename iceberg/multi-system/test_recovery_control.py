"""Failure-path controls; none of these tests call Docker or a cloud service."""

from contextlib import ExitStack
import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

import recovery_control as control

RUN_ID = "8be792ce-d8dc-47d8-a65d-3ca3774d7c1e"


def container(role="storage", *, running=True, exit_code=0):
    cpus, memory = control.CAPS.get(role, (1, control.GIB))
    return {
        "Id": role + "-id",
        "Image": role + "-image",
        "RestartCount": 0,
        "Config": {
            "Labels": {
                "com.docker.compose.project": control.PROJECT,
                "com.docker.compose.project.working_dir": str(control.ROOT),
                "com.docker.compose.service": role,
            }
        },
        "HostConfig": {
            "NanoCpus": cpus * 10**9,
            "Memory": memory,
            "MemorySwap": memory,
        },
        "NetworkSettings": {"Networks": {"owned": {"NetworkID": "internal-id"}}},
        "Mounts": [{"Type": "volume", "Destination": "/data", "Name": "owned-data"}],
        "State": {
            "Running": running,
            "Pid": 123 if running else 0,
            "ExitCode": exit_code,
            "OOMKilled": False,
            "StartedAt": "initial-generation",
            "Health": {"Status": "healthy"},
        },
    }


def client(case, role):
    directory = case / "processes" / role
    directory.mkdir(parents=True)
    value = container("recovery-writer")
    return control.Client(
        role,
        "recovery-writer",
        "owned-" + role,
        role,
        directory,
        case.name,
        identifier=role + "-id",
        initial=value,
    )


class OwnershipTests(unittest.TestCase):
    def test_controller_sigterm_uses_exception_cleanup_path(self):
        script = """
import sys, time
from pathlib import Path
import recovery_control as control
control.ROOT = Path(sys.argv[1])
def case():
    try:
        (control.ROOT / 'started').write_text('ready')
        time.sleep(60)
    finally:
        (control.ROOT / 'cleaned').write_text('finally executed')
control.storage_crash = case
sys.argv = ['controller', 'storage-crash']
control.main()
"""
        with tempfile.TemporaryDirectory() as directory:
            child = subprocess.Popen(
                [sys.executable, "-c", script, directory],
                cwd=control.ROOT,
                env={},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                deadline = time.monotonic() + 5
                while not (Path(directory) / "started").exists():
                    if child.poll() is not None or time.monotonic() > deadline:
                        self.fail("isolated controller did not enter its case")
                    time.sleep(0.01)
                child.terminate()
                _, error = child.communicate(timeout=5)
                self.assertNotEqual(child.returncode, 0)
                self.assertIn(b"SIGTERM", error)
                self.assertEqual(
                    (Path(directory) / "cleaned").read_text(), "finally executed"
                )
            finally:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_failed_inspection_is_not_fencing_or_permission_to_kill(self):
        with (
            patch.object(
                control, "inspect_client", side_effect=RuntimeError("transport failed")
            ),
            patch.object(control, "call") as mutation,
        ):
            with self.assertRaisesRegex(RuntimeError, "transport failed"):
                control.kill_client(Mock())
        mutation.assert_not_called()

    def test_wrong_project_or_working_directory_rejected(self):
        for change in (
            {"com.docker.compose.project": "other"},
            {"com.docker.compose.project.working_dir": "/somewhere/else"},
        ):
            value = container()
            value["Config"]["Labels"].update(change)
            with (
                self.subTest(change=change),
                patch.object(control, "call", return_value=json.dumps([value])),
            ):
                with self.assertRaisesRegex(RuntimeError, "foreign or unproven"):
                    control.owned("storage-id", "storage")

    def test_desktop_bind_alias_still_requires_owned_source_and_exact_witness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / "results" / RUN_ID
            case.mkdir(parents=True)
            value = container("recovery-writer")
            value["Mounts"] = [
                {
                    "Type": "bind",
                    "Source": "/host_mnt" + str(case),
                    "Destination": "/results",
                }
            ]
            with patch.object(control, "ROOT", root):
                with patch.object(control, "DESKTOP_BIND_TRANSLATION", False):
                    with self.assertRaisesRegex(RuntimeError, "foreign"):
                        control.check_mounts(value, case)
                with patch.object(control, "DESKTOP_BIND_TRANSLATION", True):
                    control.check_mounts(value, case)
                    with self.assertRaisesRegex(RuntimeError, "owned witness"):
                        control.check_mounts(value, case / "other")
                    value["Mounts"][0]["Source"] = "/host_mnt/foreign/.aws"
                    with self.assertRaisesRegex(RuntimeError, "foreign"):
                        control.check_mounts(value, case)

    def test_sigkill_proof_rejects_oom_or_unfenced_process(self):
        value = container(running=False, exit_code=137)
        control.require_fenced(value)
        for changes in (
            {"OOMKilled": True},
            {"Pid": 1},
            {"Running": True},
            {"ExitCode": 0},
        ):
            bad = copy.deepcopy(value)
            bad["State"].update(changes)
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                control.require_fenced(bad)

    def test_remote_context_rejected_without_daemon_or_mutation(self):
        endpoint = [
            {"Name": "remote", "Endpoints": {"docker": {"Host": "tcp://remote:2376"}}}
        ]
        with (
            patch.dict(control.os.environ, {}, clear=True),
            patch.object(control, "DOCKER_ENV", None),
            patch.object(
                control, "call", side_effect=["remote\n", json.dumps(endpoint)]
            ) as calls,
        ):
            with self.assertRaisesRegex(RuntimeError, "local Unix"):
                control.local_docker()
        self.assertEqual(calls.call_count, 2)

    def test_local_context_is_pinned_and_cloud_environment_not_forwarded(self):
        endpoint = [
            {
                "Name": "local",
                "Endpoints": {"docker": {"Host": "unix:///owned/docker.sock"}},
            }
        ]
        daemon = {"OSType": "linux", "NCPU": 8, "MemTotal": 8 * control.GIB}
        with (
            patch.dict(
                control.os.environ,
                {
                    "PATH": "/bin",
                    "AWS_PROFILE": "foreign",
                    "AWS_SECRET_ACCESS_KEY": "do-not-forward",
                },
                clear=True,
            ),
            patch.object(control, "DOCKER_ENV", None),
            patch.object(
                Path, "stat", return_value=SimpleNamespace(st_mode=stat.S_IFSOCK)
            ),
            patch.object(
                control,
                "call",
                side_effect=["local\n", json.dumps(endpoint), json.dumps(daemon)],
            ),
        ):
            result = control.local_docker()
            self.assertEqual(result["context"], "local")
            self.assertEqual(control.DOCKER_ENV["DOCKER_CONTEXT"], "local")
            self.assertFalse(any(key.startswith("AWS_") for key in control.DOCKER_ENV))

    def test_rendered_caps_fail_before_any_client_creation(self):
        services = {
            name: {"cpus": cpus, "mem_limit": memory, "memswap_limit": memory}
            for name, (cpus, memory) in control.CAPS.items()
        }
        services["storage"]["memswap_limit"] *= 2
        with (
            patch.object(control, "DOCKER_ENV", {}),
            patch.object(
                control, "call", return_value=json.dumps({"services": services})
            ) as calls,
        ):
            with self.assertRaisesRegex(RuntimeError, "rendered profile caps"):
                control.profile(
                    control.ROOT / "results" / RUN_ID,
                    container(),
                    container("clickhouse"),
                )
        self.assertEqual(calls.call_count, 1)
        self.assertIn("production", calls.call_args.args[0])

    def test_compose_decimal_string_memory_caps_reach_network_validation(self):
        services = {
            name: {
                "cpus": str(cpus),
                "mem_limit": str(memory),
                "memswap_limit": str(memory),
            }
            for name, (cpus, memory) in control.CAPS.items()
        }
        with (
            patch.object(control, "DOCKER_ENV", {}),
            patch.object(
                control, "call", return_value=json.dumps({"services": services})
            ) as calls,
        ):
            with self.assertRaisesRegex(RuntimeError, "profile networks"):
                control.profile(
                    control.ROOT / "results" / RUN_ID,
                    container(),
                    container("clickhouse"),
                )
        self.assertEqual(calls.call_count, 1)


class ClientLifecycleTests(unittest.TestCase):
    def gated_launch(self, root, *, bad_caps=False):
        case = root / "results" / RUN_ID
        case.mkdir(parents=True)
        value = container("recovery-writer")
        value["Config"]["Labels"]["org.examples.owner"] = control.PROJECT
        value["Mounts"] = [
            {"Type": "bind", "Source": str(case), "Destination": "/results"}
        ]
        if bad_caps:
            value["HostConfig"]["MemorySwap"] *= 2
        plan = {
            "images": {"recovery-writer": value["Image"]},
            "network_ids": {"internal-id"},
        }
        return case, value, plan

    def test_bad_actual_caps_cannot_release_a_mutating_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case, value, plan = self.gated_launch(root, bad_caps=True)
            registry = {}
            with (
                patch.object(control, "ROOT", root),
                patch.object(control, "DOCKER_ENV", {}),
                patch.object(control, "call") as launch,
                patch.object(control, "inspect_client", return_value=value),
                patch.object(control, "built_source_proof") as source_check,
            ):
                with self.assertRaisesRegex(RuntimeError, "effective CPU/memory/swap"):
                    control.start_client(
                        case,
                        "writer-000",
                        "recovery-writer",
                        "recovery-write",
                        {},
                        registry,
                        Mock(),
                        plan,
                    )
            actor = registry["writer-000"]
            self.assertTrue((actor.directory / "launch.json").exists())
            self.assertFalse((actor.directory / "START").exists())
            source_check.assert_not_called()
            self.assertNotIn("--rm", launch.call_args.args[0])

    def test_stale_built_sources_cannot_release_a_mutating_client(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case, value, plan = self.gated_launch(root)
            registry = {}
            with (
                patch.object(control, "ROOT", root),
                patch.object(control, "DOCKER_ENV", {}),
                patch.object(control, "call"),
                patch.object(control, "inspect_client", return_value=value),
                patch.object(
                    control,
                    "built_source_proof",
                    side_effect=RuntimeError("built workload sources differ"),
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError, "built workload sources differ"
                ):
                    control.start_client(
                        case,
                        "writer-000",
                        "recovery-writer",
                        "recovery-write",
                        {},
                        registry,
                        Mock(),
                        plan,
                    )
            self.assertFalse((registry["writer-000"].directory / "START").exists())

    def test_cleanup_attempts_every_client_and_preserves_existing_proofs(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / RUN_ID
            actors = {name: client(case, name) for name in ("writer-000", "writer-001")}
            (case / ("recovery-" + case.name)).mkdir()
            ended = container("recovery-writer", running=False)
            prior = {"clean": False, "original": "must remain"}
            proof = actors["writer-001"].directory / "ended.json"
            proof.write_text(json.dumps(prior))
            sampler = Mock()

            def inspect(actor):
                if actor.role == "writer-000":
                    raise RuntimeError("first inspection failed")
                return ended

            with (
                patch.object(control, "inspect_client", side_effect=inspect),
                patch.object(
                    control, "observe_client", return_value=(ended, "logs", "")
                ) as observed,
            ):
                with self.assertRaisesRegex(RuntimeError, "client cleanup failures"):
                    control.stop_actors(case, actors, sampler)
            self.assertEqual(observed.call_count, 1)
            self.assertEqual(json.loads(proof.read_text()), prior)
            self.assertEqual(sampler.unbind.call_count, 2)
            self.assertTrue((case / ("recovery-" + case.name) / "STOP").exists())

    def test_deadline_kills_all_hung_clients_and_never_calls_it_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / RUN_ID
            actors = {name: client(case, name) for name in ("writer-000", "reader-000")}
            for actor in actors.values():
                (actor.directory / "START").touch()
            running, dead = (
                container("recovery-writer"),
                container("recovery-writer", running=False, exit_code=137),
            )

            def kill(actor):
                actor.forced = True
                return dead

            with (
                patch.object(control, "inspect_client", return_value=running),
                patch.object(control, "kill_client", side_effect=kill) as killed,
                patch.object(control, "observe_client", return_value=(dead, "", "")),
            ):
                with self.assertRaisesRegex(RuntimeError, "client cleanup failures"):
                    control.stop_actors(case, actors, timeout=0)
            self.assertEqual(killed.call_count, 2)
            for actor in actors.values():
                proof = json.loads((actor.directory / "ended.json").read_text())
                self.assertTrue(proof["forced"])
                self.assertFalse(proof["clean"])

    def test_kill_requires_positive_native_exit_after_signal(self):
        running, dead = container(), container(running=False, exit_code=137)
        actor = Mock(forced=False)
        with (
            patch.object(control, "inspect_client", side_effect=[running, dead]),
            patch.object(control, "call") as call,
        ):
            self.assertEqual(control.kill_client(actor), dead)
        self.assertEqual(
            call.call_args.args[0],
            ["docker", "kill", "--signal", "SIGKILL", running["Id"]],
        )

    def test_creation_timeout_is_registered_and_original_error_survives_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = root / "results" / RUN_ID
            case.mkdir(parents=True)
            clients, sampler = {}, Mock()
            original = subprocess.TimeoutExpired(["docker", "compose", "run"], 30)
            with (
                patch.object(control, "ROOT", root),
                patch.object(control, "DOCKER_ENV", {}),
                patch.object(control, "call", side_effect=original),
                patch.object(
                    control,
                    "abort_client",
                    side_effect=RuntimeError("inspect remains unknown"),
                ) as abort,
            ):
                with self.assertRaises(subprocess.TimeoutExpired) as raised:
                    control.tool(case, "recovery-seed", clients, sampler, {})
            self.assertIs(raised.exception, original)
            self.assertEqual(len(clients), 1)
            actor = next(iter(clients.values()))
            self.assertTrue((actor.directory / "launch.json").exists())
            self.assertFalse((actor.directory / "START").exists())
            self.assertEqual(abort.call_args.args[0], actor)
            self.assertTrue(list(actor.directory.glob("cleanup-error-*.json")))
            sampler.unbind.assert_called_once_with(actor.role)

    def test_one_shot_deadline_is_fenced_and_retained_as_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            case = Path(directory) / RUN_ID
            actor = client(case, "tool-example")
            actor.service = "recovery-tools"
            running, dead = (
                container("recovery-tools"),
                container("recovery-tools", running=False, exit_code=137),
            )
            clients, sampler = {}, Mock()

            def start(case, role, service, command, extra, registry, sampler, plan):
                registry[role] = actor
                return actor

            def abort(value):
                value.aborted, value.forced = True, True
                return dead

            with (
                patch.object(control, "start_client", side_effect=start),
                patch.object(control, "wait_coverage"),
                patch.object(control, "release"),
                patch.object(control, "inspect_client", return_value=running),
                patch.object(control, "abort_client", side_effect=abort) as aborted,
                patch.object(control, "observe_client", return_value=(dead, "", "")),
            ):
                with self.assertRaisesRegex(TimeoutError, "deadline"):
                    control.tool(case, "recovery-seed", clients, sampler, {}, timeout=0)
            aborted.assert_called_once_with(actor)
            proof = json.loads((actor.directory / "ended.json").read_text())
            self.assertFalse(proof["clean"])
            self.assertTrue(proof["forced"])


class CaseFinalizationTests(unittest.TestCase):
    def setup_case(
        self,
        stack,
        root,
        *,
        progress_error=None,
        metrics_error=None,
        results_dir="results/manual",
    ):
        root = root.resolve()
        storage, query = container(), container("clickhouse")
        fenced = container(running=False, exit_code=137)
        reopened = copy.deepcopy(storage)
        reopened["State"]["StartedAt"] = "reopened-generation"
        sampler = Mock(errors=[])
        sampler.close.return_value = ([], {})
        if metrics_error:
            sampler.close.side_effect = metrics_error
        mocks = {
            "local_docker": Mock(return_value={"context": "local"}),
            "service": Mock(side_effect=[storage, query]),
            "call": Mock(
                return_value=json.dumps(
                    [
                        {
                            "Labels": {"com.docker.compose.project": control.PROJECT},
                            "Name": "owned-data",
                            "CreatedAt": "then",
                            "Mountpoint": "/local/volume",
                        }
                    ]
                )
            ),
            "profile": Mock(return_value={"network_ids": set(), "images": {}}),
            "sources": Mock(return_value={"app/example.go": "tested"}),
            "ResourceSampler": Mock(return_value=sampler),
            "release": Mock(),
            "progress": Mock(side_effect=progress_error),
            "hold_outage": Mock(),
            "wait_coverage": Mock(),
            "stop_actors": Mock(),
            "wait_storage": Mock(return_value=reopened),
            "owned": Mock(side_effect=[storage, fenced, query, query])
            if not progress_error
            else Mock(return_value=storage),
            "verification_proof": Mock(
                return_value={
                    "verification_id": RUN_ID,
                    "verification_sha256": "verified",
                }
            ),
        }

        def tool(case, command, clients, sampler, plan, **kwargs):
            if command == "recovery-seed":
                workload = case / ("recovery-" + case.name)
                workload.mkdir()
                (workload / "config.json").write_text(
                    json.dumps({"writers": 2, "readers": 2})
                )
            return ""

        def start(case, role, service, command, extra, registry, sampler, plan):
            actor = client(case, role)
            actor.service = service
            registry[role] = actor
            return actor

        mocks.update(tool=Mock(side_effect=tool), start_client=Mock(side_effect=start))
        stack.enter_context(patch.object(control, "ROOT", root))
        stack.enter_context(patch.object(control, "DOCKER_ENV", {}))
        stack.enter_context(
            patch.dict(control.os.environ, {"RESULTS_DIR": results_dir})
        )
        stack.enter_context(
            patch.object(
                control.shutil,
                "disk_usage",
                return_value=SimpleNamespace(free=20 * control.GIB),
            )
        )
        stack.enter_context(
            patch.object(control.uuid, "uuid4", return_value=uuid.UUID(RUN_ID))
        )
        for name, mock in mocks.items():
            stack.enter_context(patch.object(control, name, mock))
        return root / results_dir / "production" / RUN_ID, sampler, mocks

    def test_non_default_results_directory_is_owned_by_the_case(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, _, _ = self.setup_case(
                stack, Path(directory), results_dir="results/nested/custom-run"
            )
            control.storage_crash()
            self.assertTrue(json.loads((case / "result.json").read_text())["passed"])
            self.assertFalse((Path(directory) / "results/manual").exists())

    def test_fixture_registration_failure_blocks_every_workload_mutation(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, _, mocks = self.setup_case(stack, Path(directory))
            stack.enter_context(
                patch(
                    "recovery_cleanup.register_fixture",
                    side_effect=OSError("fixture index could not be fsynced"),
                )
            )
            with self.assertRaisesRegex(OSError, "could not be fsynced"):
                control.storage_crash()
            mocks["tool"].assert_not_called()
            mocks["start_client"].assert_not_called()
            mocks["ResourceSampler"].assert_not_called()
            self.assertTrue((case / "case.json").exists())
            self.assertFalse((case / "result.json").exists())

    def test_preflight_failure_does_not_create_unregistered_production_case(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, _, mocks = self.setup_case(stack, Path(directory))
            mocks["profile"].side_effect = RuntimeError("unsafe profile")
            with self.assertRaisesRegex(RuntimeError, "unsafe profile"):
                control.storage_crash()
            self.assertFalse(case.exists())
            mocks["tool"].assert_not_called()
            mocks["ResourceSampler"].assert_not_called()

    def test_metrics_failure_cannot_publish_a_pass(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, sampler, mocks = self.setup_case(
                stack, Path(directory), metrics_error=OSError("recorder failed")
            )
            with self.assertRaisesRegex(RuntimeError, "cleanup/metrics incomplete"):
                control.storage_crash()
            self.assertFalse((case / "result.json").exists())
            self.assertFalse(json.loads((case / "failure.json").read_text())["passed"])
            sampler.close.assert_called_once()
            self.assertEqual(mocks["stop_actors"].call_count, 2)

    def test_original_fault_survives_client_and_sampler_cleanup_failures(self):
        original = ValueError("original independent-reader mismatch")
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, sampler, mocks = self.setup_case(
                stack,
                Path(directory),
                progress_error=original,
                metrics_error=OSError("metrics failure"),
            )
            mocks["stop_actors"].side_effect = RuntimeError("client cleanup failed")
            with self.assertRaises(ValueError) as raised:
                control.storage_crash()
            self.assertIs(raised.exception, original)
            self.assertIn(
                "original independent-reader mismatch",
                (case / "failure.json").read_text(),
            )
            self.assertTrue((case / "cleanup-errors.json").exists())
            self.assertFalse((case / "result.json").exists())
            sampler.close.assert_called_once()

    def test_phase_record_failure_still_stops_sampler_and_blocks_pass(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, sampler, _ = self.setup_case(stack, Path(directory))
            phase = control.phase

            def emit(case, name):
                if name == "finished":
                    raise OSError("phase journal failed")
                return phase(case, name)

            stack.enter_context(patch.object(control, "phase", side_effect=emit))
            with self.assertRaisesRegex(RuntimeError, "cleanup/metrics incomplete"):
                control.storage_crash()
            sampler.close.assert_called_once()
            self.assertFalse((case / "result.json").exists())

    def test_pass_is_published_only_after_final_client_and_resource_checks(self):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            case, sampler, mocks = self.setup_case(stack, Path(directory))

            def close():
                self.assertFalse((case / "result.json").exists())
                self.assertEqual(mocks["stop_actors"].call_count, 2)
                return [], {}

            sampler.close.side_effect = close
            control.storage_crash()
            self.assertTrue(json.loads((case / "result.json").read_text())["passed"])
            self.assertFalse((case / "failure.json").exists())


if __name__ == "__main__":
    unittest.main()
