"""Open only the verified restored copy with pinned SeaweedFS 4.54, never downgrade it."""

import argparse
import fcntl
import json
import re
import uuid

import cold_readback as readback
import cold_storage as cold
import recovery_cleanup as cleanup
import recovery_control as control


def prepare(parent_path, case, verification):
    control.local_docker()
    parent, invocation, restored = readback.checkpoint(parent_path)
    # Invalid witness/baseline input must not reserve the volume for an upgrade.
    readback.fixture(case, verification, invocation["source_volume"])
    baseline = invocation["services"]["storage"]["image_id"]
    if baseline != readback.BASELINE_453_IMAGE:
        raise RuntimeError(
            "cold-upgrade supports only the pinned 4.53 to 4.54 transition"
        )
    image_id = readback.UPGRADE_454_IMAGE
    cached = control.call(
        ["docker", "image", "ls", "--no-trunc", "--format", "{{.ID}}"]
    )
    if image_id not in cached.split():
        control.call(["docker", "pull", "chrislusf/seaweedfs@" + image_id], timeout=180)
    (image,) = json.loads(control.call(["docker", "image", "inspect", image_id]))
    if (
        image["Id"] != image_id
        or image["Os"] != "linux"
        or image["Architecture"] != "arm64"
    ):
        raise RuntimeError("require the pinned native SeaweedFS 4.54 image")
    version = control.call(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--cpus",
            "1",
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--entrypoint",
            "/usr/bin/weed",
            image_id,
            "version",
        ]
    )
    if not re.search(r"\b4\.54\b", version) or "5ef99c1" not in version:
        raise RuntimeError("actual storage binary differs from the pinned release")
    marker = cleanup.regular(parent / "storage-image-transition.json", parent)
    if not marker.exists():
        if cold.volume_users(restored["Name"]):
            raise RuntimeError("fence every restore owner before registering upgrade")
        control.save(
            marker,
            {
                "transition_id": str(uuid.uuid4()),
                "checkpoint_id": parent.name,
                "restore_volume": restored,
                "source_image": baseline,
                "target_image": image_id,
                "target_version": "4.54",
                "actual_version": version.strip(),
                "archive_sha256": cleanup.digest(parent / "storage.tar.gz"),
                "scope": "Conservative reservation before opening restored storage. It blocks older-image reuse even if startup fails; rollback requires a new restore, not an in-place downgrade.",
            },
        )
    intent = cleanup.load(marker, parent)
    readback.storage_selection(
        parent, invocation, restored, image_id, intent["transition_id"]
    )
    if intent["archive_sha256"] != cleanup.digest(parent / "storage.tar.gz"):
        raise RuntimeError("pre-upgrade recovery archive changed")
    return image_id, intent["transition_id"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--verification", required=True)
    args = parser.parse_args()
    with cold.controller_lock() as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        image, transition = prepare(args.checkpoint, args.case, args.verification)
        readback.run(
            args.checkpoint,
            args.case,
            args.verification,
            storage_image=image,
            transition=transition,
        )


if __name__ == "__main__":
    main()
