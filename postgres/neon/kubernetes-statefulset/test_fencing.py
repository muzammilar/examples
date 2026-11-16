"""Transport failure must never authorize deleting durable storage."""

import json
import subprocess
import unittest
from unittest.mock import patch

from fencing import prove_stopped


class Fencing(unittest.TestCase):
    def setUp(self):
        self.pod = {
            "spec": {"nodeName": "owned-worker"},
            "status": {"containerStatuses": [{"containerID": "containerd://old"}]},
        }
        self.owner = json.dumps(
            [{"Config": {"Labels": {"io.x-k8s.kind.cluster": "neon-storage"}}}]
        )

    def response(self, state):
        return subprocess.CompletedProcess(
            [], 0, json.dumps({"status": {"state": state}}), ""
        )

    def test_native_exit(self):
        with (
            patch("fencing.subprocess.check_output", return_value=self.owner),
            patch(
                "fencing.subprocess.run", return_value=self.response("CONTAINER_EXITED")
            ),
        ):
            self.assertEqual(
                prove_stopped(self.pod)["native_state"], "CONTAINER_EXITED"
            )

    def test_daemon_failure_is_not_fencing(self):
        with (
            patch("fencing.subprocess.check_output", return_value=self.owner),
            patch(
                "fencing.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 1, "", "Docker daemon unreachable"
                ),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot prove"):
                prove_stopped(self.pod)

    def test_not_found_requires_healthy_empty_listing(self):
        with (
            patch(
                "fencing.subprocess.check_output",
                side_effect=[self.owner, '{"containers":[]}'],
            ),
            patch(
                "fencing.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 1, "", "rpc error: code = NotFound"
                ),
            ),
        ):
            self.assertEqual(
                prove_stopped(self.pod)["native_state"], "absent-from-healthy-cri"
            )

    def test_not_found_followed_by_runtime_failure_is_not_fencing(self):
        with (
            patch(
                "fencing.subprocess.check_output",
                side_effect=[
                    self.owner,
                    subprocess.CalledProcessError(1, ["crictl", "ps"]),
                ],
            ),
            patch(
                "fencing.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 1, "", "rpc error: code = NotFound"
                ),
            ),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                prove_stopped(self.pod)

    def test_running_container_is_not_fenced(self):
        with (
            patch("fencing.subprocess.check_output", return_value=self.owner),
            patch(
                "fencing.subprocess.run",
                return_value=self.response("CONTAINER_RUNNING"),
            ),
            patch("fencing.time.monotonic", side_effect=[0, 0, 2]),
            patch("fencing.time.sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                prove_stopped(self.pod, timeout=1)

    def test_foreign_node_is_rejected(self):
        with patch(
            "fencing.subprocess.check_output", return_value='[{"Config":{"Labels":{}}}]'
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign"):
                prove_stopped(self.pod)
