"""Audit committed input positions for both paths at the retained source fence."""

import json
import re


def audit(root, compose, path, fixture):
    source = []
    for folder in (root / "results").glob("source-*"):
        identity = folder / "fixture.json"
        if identity.exists() and json.loads(identity.read_text())["id"] == fixture:
            source.append(folder)
    if len(source) != 1:
        raise RuntimeError("Exactly one source tape required")
    folder = source[0]
    expected = {}
    for file in folder.glob("*-source-fence.txt"):
        for line in file.read_text().splitlines():
            if line.startswith("cdc.public."):
                topic, partition, end = line.split(":")
                expected[(topic, int(partition))] = int(end)
    flink = re.findall(
        "'properties.group.id'='([^']+)'", (folder / "single-sink.sql").read_text()
    )
    if len(flink) != 2 or any(
        not re.fullmatch("control_(orders|customers)_[a-f0-9]{32}", group)
        for group in flink
    ):
        raise RuntimeError("Unexpected owned source groups")
    groups = [("doris", group) for group in flink] + [
        ("clickhouse", "orders-control-orders"),
        ("clickhouse", "orders-control-customers"),
    ]
    report = []
    for backend, group in groups:
        output = compose(
            "exec",
            "-T",
            "-e",
            "KAFKA_HEAP_OPTS=-Xms32m -Xmx64m",
            "controller1",
            "/opt/kafka/bin/kafka-consumer-groups.sh",
            "--bootstrap-server",
            "broker1:9092",
            "--describe",
            "--group",
            group,
        )
        (path / (group + "-committed.txt")).write_text(output)
        observed = {}
        for line in output.splitlines():
            fields = line.split()
            if (
                len(fields) >= 6
                and fields[0] == group
                and fields[1].startswith("cdc.public.")
            ):
                key = (fields[1], int(fields[2]))
                current, end, lag = map(int, fields[3:6])
                if (
                    key not in expected
                    or current != expected[key]
                    or end != expected[key]
                    or lag != 0
                ):
                    raise RuntimeError("Committed group does not match source cut")
                observed[key] = current
        topic = "cdc.public.customers" if "customers" in group else "cdc.public.orders"
        if set(observed) != {key for key in expected if key[0] == topic}:
            raise RuntimeError("Incomplete consumer group evidence")
        report.append(
            {
                "backend": backend,
                "group": group,
                "partitions": [
                    {"topic": k[0], "partition": k[1], "committed_offset": v}
                    for k, v in sorted(observed.items())
                ],
            }
        )
    (path / "committed-source-fences.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report
