"""Interpret native placement/identity; never invent an application hash ring."""

from uuid import UUID


def validate(value):
    if str(UUID(value["fsid"])) != value["fsid"] or set(value["guests"]) != {
        "0",
        "1",
        "2",
    }:
        raise RuntimeError("missing native cluster/guest identity")
    if len(value["osds"]) != 9 or len({v["uuid"] for v in value["osds"].values()}) != 9:
        raise RuntimeError("missing or duplicate OSD identity")
    if any(str(UUID(v["uuid"])) != v["uuid"] for v in value["osds"].values()):
        raise RuntimeError("invalid native OSD UUID")
    for node, guest in value["guests"].items():
        UUID(guest["boot_id"])
        osds = {
            number
            for number, osd in value["osds"].items()
            if osd["host"] == f"ceph{node}"
        }
        if len(osds) != 3:
            raise RuntimeError("expected three native OSDs on each guest")
        expected = {
            f"ceph-mon@ceph{node}.service",
            f"ceph-radosgw@rgw.ceph{node}.service",
        }
        expected.update(f"ceph-osd@{number}.service" for number in osds)
        if node in {"0", "1"}:
            expected.add(f"ceph-mgr@ceph{node}.service")
        if set(guest["units"]) != expected:
            raise RuntimeError("incomplete native service inventory")
        for unit in guest["units"].values():
            if (
                unit["ActiveState"] != "active"
                or unit["SubState"] != "running"
                or int(unit["MainPID"]) <= 0
                or int(unit["ExecMainStartTimestampMonotonic"]) <= 0
            ):
                raise RuntimeError("native service not running")
    if not value["versions"] or any(
        not version.startswith("ceph version 20.2.5 ")
        for role in value["versions"].values()
        for version in role
    ):
        raise RuntimeError("native daemon version differs from the pinned release")


def identities(value):
    validate(value)
    return {key: value[key] for key in ["fsid", "osds"]}


def survivors(before, after, *, guest=None, unit=None, replaced_osd=None):
    validate(before)
    validate(after)
    if before["fsid"] != after["fsid"] or set(before["osds"]) != set(after["osds"]):
        raise RuntimeError("native cluster/OSD set changed")
    for number, value in before["osds"].items():
        new = after["osds"][number]
        if number == replaced_osd and new["uuid"] == value["uuid"]:
            raise RuntimeError("replacement reused the old OSD UUID")
        if new["host"] != value["host"] or (
            number != replaced_osd and new["uuid"] != value["uuid"]
        ):
            raise RuntimeError("unrelated native OSD identity changed")
    for node, value in before["guests"].items():
        new = after["guests"][node]
        if node == guest:
            if value["boot_id"] == new["boot_id"]:
                raise RuntimeError("declared crashed guest did not reboot")
            continue
        if value["boot_id"] != new["boot_id"]:
            raise RuntimeError("surviving guest rebooted")
        for name, old in value["units"].items():
            if (node, name) == unit or (
                replaced_osd is not None and name == f"ceph-osd@{replaced_osd}.service"
            ):
                continue
            if old != new["units"][name]:
                raise RuntimeError("surviving native process restarted: " + name)
