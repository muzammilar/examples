"""Hold down one owned MDS process; never format, reset or force the FSMap."""

import json
import sys
from guest import ssh


def main():
    action, name = sys.argv[1:3]
    if name not in ("fs0", "fs1"):
        raise ValueError("Unowned metadata server")
    node = int(name[-1])
    unit = "ceph-mds@" + name + ".service"
    if action == "kill":
        # Mask runtime restart before SIGKILL so this crosses the detection grace.
        pid = int(
            ssh(node, "systemctl show " + unit + " --value -p MainPID", True).strip()
        )
        if pid <= 1:
            raise RuntimeError("MDS has no live native PID")
        command = ssh(node, 'tr "\\0" " " < /proc/' + str(pid) + "/cmdline", True)
        if "ceph-mds" not in command:
            raise RuntimeError("PID does not identify MDS")
        ssh(
            node,
            "systemctl mask --runtime "
            + unit
            + "; systemctl kill --signal=SIGKILL --kill-who=main "
            + unit,
        )
        raw = ssh(
            node,
            "systemctl show "
            + unit
            + " -p MainPID -p ActiveState -p SubState -p ExecMainCode -p ExecMainStatus",
            True,
        )
        state = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
        if state["ExecMainStatus"] != "9" or state["ExecMainCode"] != "2":
            raise RuntimeError("Native MDS SIGKILL not proved: " + str(state))
        if (
            ssh(node, "test ! -e /proc/" + str(pid) + " && echo absent", True).strip()
            != "absent"
        ):
            raise RuntimeError("Old MDS PID remains")
        print(
            json.dumps(
                {
                    "node": node,
                    "name": name,
                    "unit": unit,
                    "pid": pid,
                    "cmdline": command,
                    "state": state,
                    "kill_proven": True,
                }
            )
        )
    elif action == "restore":
        ssh(
            node,
            "systemctl unmask --runtime "
            + unit
            + "; systemctl reset-failed "
            + unit
            + " || true; systemctl start "
            + unit,
        )
        print(json.dumps({"restored": name}))
    else:
        raise ValueError(action)


if __name__ == "__main__":
    main()
