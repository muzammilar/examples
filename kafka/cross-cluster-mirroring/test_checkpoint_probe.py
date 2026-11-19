import unittest

from checkpoint_probe import parse_checkpoints


def record(partition, upstream=10, downstream=12, group="dr-test", topic="orders-123"):
    return (
        f"Checkpoint{{consumerGroupId={group}, topicPartition={topic}-{partition}, "
        f"upstreamOffset={upstream}, downstreamOffset={downstream}, metadata=}}"
    )


class CheckpointTests(unittest.TestCase):
    def test_uses_latest_native_record_per_partition(self):
        body = "\n".join([record(p) for p in range(6)] + [record(2, 20, 25)])
        self.assertEqual(parse_checkpoints(body, "dr-test", "orders-123")[2], (20, 25))

    def test_does_not_mix_groups_or_prefixed_topics(self):
        body = "\n".join(
            [record(p) for p in range(5)]
            + [record(5, group="dr-other"), record(5, topic="east.orders-123")]
        )
        with self.assertRaises(ValueError):
            parse_checkpoints(body, "dr-test", "orders-123")

    def test_rejects_malformed_and_unknown_partition(self):
        valid = "\n".join(record(p) for p in range(6))
        for body in [
            valid + "\nnot a checkpoint",
            valid + "\n" + record(6),
            valid.replace("upstreamOffset=10", "upstreamOffset=-1"),
        ]:
            with self.assertRaises(ValueError):
                parse_checkpoints(body, "dr-test", "orders-123")


if __name__ == "__main__":
    unittest.main()
