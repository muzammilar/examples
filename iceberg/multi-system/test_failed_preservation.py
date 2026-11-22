"""Preservation must fail closed on changed evidence and unknown runtime state."""

import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

import failed_preservation as preservation


class FailedPreservation(unittest.TestCase):
    def test_shared_source_links_require_a_tracked_target_inside_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory).resolve()
            repository = temporary / "repository"
            root = repository / "iceberg" / "multi-system"
            root.mkdir(parents=True)
            target = repository / "compactor.py"
            target.write_text("tracked source")
            (root / "shared.py").symlink_to(target)
            outside = temporary / "outside.py"
            outside.write_text("outside repository")
            (root / "outside.py").symlink_to(outside)
            with (
                patch.object(preservation.control, "ROOT", root),
                patch.object(
                    preservation.control, "call", return_value="compactor.py"
                ) as call,
            ):
                self.assertEqual(preservation.controller_source("shared.py"), target)
                call.assert_called_once()
                call.side_effect = RuntimeError("untracked target")
                with self.assertRaisesRegex(RuntimeError, "untracked target"):
                    preservation.controller_source("shared.py")
                with self.assertRaisesRegex(RuntimeError, "escaped owned directory"):
                    preservation.controller_source("outside.py")

    def test_newer_incomplete_receipt_cannot_fall_back_to_an_older_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / str(uuid.uuid4())
            for started, data_passed in ((10, True), (20, False)):
                attempt = case / preservation.DIRECTORY / str(uuid.uuid4())
                attempt.mkdir(parents=True)
                invocation = {
                    "run_id": case.name,
                    "archive_id": attempt.name,
                    "started_ns": started,
                }
                (attempt / "invocation.json").write_text(json.dumps(invocation))
                (attempt / "receipt.json").write_text(
                    json.dumps(
                        {
                            "passed": False,
                            "original_status": "failed",
                            "data_preservation_passed": data_passed,
                            "teardown_authorized": False,
                            "run_id": case.name,
                            "invocation": invocation,
                        }
                    )
                )
            with patch.object(preservation.cleanup, "results_root", return_value=root):
                with self.assertRaisesRegex(RuntimeError, "receipt is incomplete"):
                    preservation.replay(case)

    def test_references_cannot_escape_or_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "target").write_text("evidence")
            (root / "link").symlink_to(root / "target")
            for name in ("../target", str(root / "target"), "link"):
                with self.subTest(name=name), self.assertRaises(RuntimeError):
                    preservation.local_path(root, name)

    def test_corrupt_preserved_source_and_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            case = root / "6191b730-b1aa-4cbf-8371-13952cf26a54"
            case.mkdir()
            source = case / "source"
            source.mkdir()
            (source / "source.py").write_text("original source")
            digest = preservation.cleanup.digest

            def save(name, value):
                (case / name).write_text(json.dumps(value))

            files = {"source.py": digest(source / "source.py")}
            save(
                "case.json",
                {
                    "run_id": case.name,
                    "project": preservation.control.PROJECT,
                    "kind": "iceberg-compactor-crash",
                    "source_sha256": files,
                },
            )
            save("failure.json", {"passed": False, "native_storage_preserved": True})
            save(
                "source-seal.json",
                {
                    "run_id": case.name,
                    "case_sha256": digest(case / "case.json"),
                    "files": files,
                },
            )
            save(
                "original-seal.json",
                {
                    "run_id": case.name,
                    "files": {
                        "failure.json": {
                            "sha256": digest(case / "failure.json"),
                            "bytes": (case / "failure.json").stat().st_size,
                        }
                    },
                },
            )
            plan = {
                "case": case.name,
                "case_sha256": digest(case / "case.json"),
                "source_seal": {
                    "file": "source-seal.json",
                    "sha256": digest(case / "source-seal.json"),
                    "source_root": "source",
                },
                "original_seal": {
                    "file": "original-seal.json",
                    "sha256": digest(case / "original-seal.json"),
                },
            }
            with (
                patch.object(preservation, "plan_for", return_value=plan),
                patch.object(preservation.control, "ROOT", root),
                patch.object(preservation.cleanup, "results_root", return_value=root),
            ):
                (source / "source.py").write_text("rewritten source")
                with self.assertRaisesRegex(RuntimeError, "original source changed"):
                    preservation.original_evidence(case)
                (source / "source.py").write_text("original source")
                (case / "failure.json").write_text(
                    json.dumps(
                        {
                            "passed": False,
                            "native_storage_preserved": True,
                            "extra": "tampered",
                        }
                    )
                )
                with self.assertRaisesRegex(RuntimeError, "original artifact changed"):
                    preservation.original_evidence(case)

    def test_runtime_listing_error_never_proves_container_absence(self):
        with patch.object(
            preservation.control, "call", side_effect=OSError("runtime unavailable")
        ):
            with self.assertRaises(OSError):
                preservation.native_fences(Path("case"), [])

    def test_listed_container_inspection_error_is_not_absence(self):
        client = {
            "directory": "processes/a",
            "launch": {"service": "recovery-tools"},
            "record": {"id": "a" * 64},
        }
        with (
            patch.object(preservation.control, "call", side_effect=["a" * 64, ""]),
            patch.object(
                preservation.control, "owned", side_effect=OSError("transport failed")
            ),
        ):
            with self.assertRaises(OSError):
                preservation.native_fences(Path("case"), [client])


if __name__ == "__main__":
    unittest.main()
