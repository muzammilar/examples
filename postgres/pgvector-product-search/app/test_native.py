import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import production
from workload import META


class NativeControls(unittest.TestCase):
    def native(self):
        settings = {
            k: "on"
            for k in [
                "fsync",
                "full_page_writes",
                "data_checksums",
                "synchronous_commit",
            ]
        }
        primary = dict(
            role="primary",
            system_identifier="42",
            timeline=2,
            settings=settings,
            replication=[
                dict(
                    name="pg1", state="streaming", sync_state="sync", replay_lsn="0/20"
                ),
                dict(
                    name="pg2", state="streaming", sync_state="async", replay_lsn="0/20"
                ),
            ],
        )
        primary["patroni_config"] = {
            "synchronous_mode": True,
            "synchronous_mode_strict": True,
            "synchronous_node_count": 1,
            "check_timeline": True,
            "maximum_lag_on_failover": 0,
        }
        replica = dict(
            role="replica",
            system_identifier="42",
            replay_lsn="0/20",
            receiver=[dict(status="streaming", timeline=2)],
        )
        return dict(pg0=primary, pg1=copy.deepcopy(replica), pg2=copy.deepcopy(replica))

    def test_live_repair_waits_for_replay_with_writer_active(self):
        initial = self.native()
        initial["pg0"]["flush_lsn"] = "0/30"
        caught_up = self.native()
        caught_up["pg0"]["flush_lsn"] = "0/40"
        for host in ["pg1", "pg2"]:
            caught_up[host]["replay_lsn"] = "0/30"
        for peer in caught_up["pg0"]["replication"]:
            peer["replay_lsn"] = "0/30"
        with (
            patch.object(production, "HOSTS", ["pg0", "pg1", "pg2"]),
            patch.object(
                production, "state", side_effect=[initial, caught_up]
            ) as sample,
            patch.object(production.time, "sleep"),
        ):
            result = production.wait_healthy()
            self.assertEqual(sample.call_count, 2)
            self.assertEqual(result["pg0"]["recovery_barrier"]["flush_lsn"], "0/30")
            # A moving primary WAL tip must not make catch-up impossible under continuing writes.
            self.assertEqual(result["pg0"]["flush_lsn"], "0/40")

    def test_network_failure_is_not_password_rejection(self):
        import psycopg

        for message in [
            "connection refused",
            "timeout expired",
            'password authentication failed for user "foreign"',
        ]:
            self.assertFalse(
                production.password_rejected(psycopg.OperationalError(message))
            )
        self.assertTrue(
            production.password_rejected(
                psycopg.OperationalError(
                    'FATAL: password authentication failed for user "postgres"'
                )
            )
        )

    def test_native_barrier(self):
        states = self.native()
        with patch.object(production, "HOSTS", ["pg0", "pg1", "pg2"]):
            self.assertEqual(production.healthy(states, "0/20", "pg0"), "pg0")
            for mutation in [
                "lag",
                "timeline",
                "identity",
                "missing",
                "sync",
                "fsync",
                "split_brain",
            ]:
                bad = copy.deepcopy(states)
                if mutation == "lag":
                    bad["pg2"]["replay_lsn"] = "0/19"
                elif mutation == "timeline":
                    bad["pg2"]["receiver"][0]["timeline"] = 1
                elif mutation == "identity":
                    bad["pg2"]["system_identifier"] = "foreign"
                elif mutation == "missing":
                    bad["pg2"] = {"error": "not ready"}
                elif mutation == "sync":
                    bad["pg0"]["replication"][0]["sync_state"] = "async"
                elif mutation == "fsync":
                    bad["pg0"]["settings"]["fsync"] = "off"
                elif mutation == "split_brain":
                    bad["pg2"]["role"] = "primary"
                with (
                    self.subTest(mutation=mutation),
                    self.assertRaises((RuntimeError, KeyError)),
                ):
                    production.healthy(bad, "0/20", "pg0")

    def test_witness_rejects_orphan_and_duplicate_outcomes(self):
        row = {"id": "0", "tenant": 1, "stock": 1, "price_cents": 101, "version": 1}
        attempt = dict(stage="attempt", rows=[row], request_id="1")
        ack = dict(stage="ack", rows=[row], request_id="1")
        for events in [[ack], [attempt, ack, ack], [attempt, attempt]]:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                case = root / "cases" / "one"
                case.mkdir(parents=True)
                (case / "writer.jsonl").write_text(
                    "".join(json.dumps(e) + "\n" for e in events)
                )
                with (
                    patch.object(production, "ROOT", root),
                    self.assertRaises(RuntimeError),
                ):
                    production.histories()

    def test_pending_writer_is_not_quiescent(self):
        from unittest.mock import MagicMock
        import psycopg

        context = MagicMock()
        connection = context.__enter__.return_value
        connection.execute.side_effect = [[(7, "active", "IPC", "SyncRep")], []]
        with (
            patch.object(production, "HOSTS", ["pg0"]),
            patch.object(production, "connect", return_value=context),
            patch.object(production.time, "sleep"),
        ):
            self.assertEqual(production.writer_quiescence(), {"pg0": []})
            self.assertEqual(connection.execute.call_count, 2)
        connection.execute.side_effect = None
        connection.execute.return_value = [(7, "active", "IPC", "SyncRep")]
        with (
            patch.object(production, "HOSTS", ["pg0"]),
            patch.object(production, "connect", return_value=context),
            patch.object(production.time, "monotonic", side_effect=[0, 31]),
            self.assertRaises(RuntimeError),
        ):
            production.writer_quiescence()
        with (
            patch.object(production, "HOSTS", ["pg0"]),
            patch.object(
                production,
                "connect",
                side_effect=psycopg.OperationalError("unknown connection state"),
            ),
            self.assertRaises(psycopg.OperationalError),
        ):
            production.writer_quiescence()

    def test_unanswered_intent_is_ambiguous_not_ack(self):
        row = {"id": "0", "tenant": 1, "stock": 1, "price_cents": 101, "version": 1}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            case = root / "cases" / "one"
            case.mkdir(parents=True)
            (case / "writer.jsonl").write_text(
                json.dumps(dict(stage="attempt", rows=[row], request_id="1")) + "\n"
            )
            with patch.object(production, "ROOT", root):
                intents, acks, counts, _ = production.histories()
            self.assertEqual(counts["ambiguous_rows"], 1)
            self.assertEqual(acks, {})
            self.assertEqual(intents[("0", 1)], row)

    def test_complete_vector_and_body_oracle(self):
        base = {k: "" for k in META}
        base.update(id="0", tenant=1, stock=5, price_cents=100, version=0)

        class Fixture:
            vectors = np.zeros((1, 384), dtype="<f4")

            def row(self, pid, version=0):
                result = base.copy()
                if version:
                    result.update(
                        stock=version % 100, price_cents=100 + version, version=version
                    )
                return result

        fixture = Fixture()
        data = {
            k: base[k]
            for k in META
            if k not in ["id", "tenant", "stock", "price_cents", "version"]
        }

        def checked(
            vector=None,
            version=0,
            tenant=1,
            stock=5,
            price=100,
            acks=None,
            intents=None,
        ):
            return production.check_row(
                fixture,
                0,
                data,
                tenant,
                stock,
                price,
                version,
                fixture.vectors[0] if vector is None else vector,
                intents or {},
                acks or {},
            )

        checked()
        from pgvector import Vector

        checked(vector=Vector(fixture.vectors[0]))
        for bad in [np.zeros(383, dtype="<f4"), np.ones(384, dtype="<f4")]:
            with self.assertRaises(RuntimeError):
                checked(vector=bad)
        signed_zero = np.zeros(384, dtype="<f4")
        signed_zero[-1] = -0.0
        with self.assertRaises(RuntimeError):
            checked(vector=signed_zero)
        with self.assertRaises(RuntimeError):
            checked(tenant=2)
        with self.assertRaises(RuntimeError):
            checked(acks={"0": 1})
        with self.assertRaises(RuntimeError):
            checked(version=1, stock=1, price=101)
        checked(
            version=1,
            stock=1,
            price=101,
            intents={
                ("0", 1): dict(id="0", tenant=1, stock=1, price_cents=101, version=1)
            },
        )


if __name__ == "__main__":
    unittest.main()
