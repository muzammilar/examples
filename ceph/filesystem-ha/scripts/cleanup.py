"""Verify/export before removal, and retire the fixture only after owned teardown."""

import json
import os
import subprocess
import sys
import time
import lab
import test as checks


def main():
    mode = sys.argv[1]
    data = json.loads((lab.ROOT / "results/fixture.json").read_text())
    receipt = lab.ROOT / "results" / ("down-" + data["id"] + ".json")
    if mode == "down":
        if receipt.exists():
            raise RuntimeError("Deployment already removed; use make docker-down")
        directory = lab.ROOT / "results" / ("teardown-" + str(time.time_ns()))
        directory.mkdir()
        kernel_environment = lab.ROOT / "results/kernel-client/environment.json"
        if kernel_environment.exists():
            if json.loads(kernel_environment.read_text())["fsid"] != data["fsid"]:
                raise RuntimeError("Kernel environment belongs to another cluster")
            subprocess.run(
                [
                    sys.executable,
                    str(lab.ROOT / "projects/kernel-client/control.py"),
                    "down",
                ],
                check=True,
            )
        # Any failed reconciliation preserves the running infrastructure for diagnosis.
        verified = checks.audit(directory)
        removed = json.loads(lab.native("remove_filesystem.py", data["fsid"]).stdout)
        checks.save(
            receipt,
            {
                "fixture": data["id"],
                "fsid": data["fsid"],
                "verified": verified,
                "removed": removed,
                "evidence": str(directory.relative_to(lab.ROOT)),
            },
        )
    elif mode == "docker-down":
        proof = json.loads(receipt.read_text())
        if proof["fixture"] != data["id"] or proof["fsid"] != data["fsid"]:
            raise RuntimeError("Cleanup receipt differs from fixture")
        keep_image = os.environ.get("KEEP_IMAGE", "0") == "1"
        removal = lab.command(
            "--profile",
            "tools",
            "down",
            "--volumes",
            "--remove-orphans",
            *([] if keep_image else ["--rmi", "local"]),
        )
        (
            lab.ROOT / "results" / ("docker-removal-" + str(time.time_ns()) + ".log")
        ).write_text(removal.stdout + removal.stderr)
        if lab.command("ps", "--all", "--quiet").stdout.strip():
            raise RuntimeError("Owned containers remain")
        # Refuse to retire the only fixture index unless the owned volume is gone.
        volume = subprocess.run(
            ["docker", "volume", "inspect", "ceph-filesystem-ha_state"],
            capture_output=True,
            text=True,
            check=False,
        )
        if volume.returncode == 0:
            raise RuntimeError("Owned state volume still exists")
        if "no such volume" not in volume.stderr.lower():
            raise RuntimeError(
                "Could not verify owned volume removal: " + volume.stderr
            )
        image = "ceph-filesystem-ha-guests:21.2.0"
        listing = ["docker", "image", "ls", "--filter", "reference=" + image, "--quiet"]
        if not keep_image and subprocess.check_output(listing, text=True).strip():
            subprocess.run(["docker", "image", "rm", image], check=True)
        if not keep_image and subprocess.check_output(listing, text=True).strip():
            raise RuntimeError("Owned build tag remains")
        index = lab.ROOT / "results/fixture.json"
        for name in ("fixture", "environment"):
            path = lab.ROOT / "results/kernel-client" / (name + ".json")
            if path.exists():
                path.rename(
                    path.parent / ("retired-" + name + "-" + data["id"] + ".json")
                )
        index.rename(lab.ROOT / "results" / ("retired-fixture-" + data["id"] + ".json"))
        checks.save(
            lab.ROOT / "results" / ("destroyed-" + data["id"] + ".json"),
            {
                "fixture": data["id"],
                "containers_absent": True,
                "volume_absent": True,
                "image_retained_for_restore": keep_image,
            },
        )
    else:
        raise ValueError(mode)
    print(json.dumps({"mode": mode, "passed": True}))


if __name__ == "__main__":
    main()
