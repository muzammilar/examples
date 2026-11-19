import os
import time
import threading

import httpx
import psycopg
from psycopg_pool import ConnectionPool
from psycopg.types.json import Jsonb
from pgvector.psycopg import register_vector

from workload import META, main, oracle


class Adapter:
    modes = ["dense", "lexical", "hybrid"]

    def __init__(self, fixture, hosts=None):
        self.fixture = fixture
        self.hosts = hosts or os.environ.get("PG_NODES", "pg0,pg1,pg2").split(",")
        self.http = httpx.Client(timeout=3)
        self.pools = {}
        self.pool_lock = threading.Lock()
        self.cached_leader = None
        self.leader_until = 0

    def leader(self):
        if os.environ.get("MODE", "cluster") == "single":
            return self.hosts[0]
        if self.cached_leader and time.monotonic() < self.leader_until:
            return self.cached_leader
        for host in self.hosts:
            try:
                if self.http.get(f"http://{host}:8008/primary").status_code == 200:
                    self.cached_leader = host
                    self.leader_until = time.monotonic() + 0.5
                    return host
            except httpx.HTTPError:
                pass
        raise RuntimeError("no primary; no mutation sent")

    def connect(self, host=None):
        host = host or self.leader()
        with self.pool_lock:
            if host not in self.pools:
                self.pools[host] = ConnectionPool(
                    conninfo=f"host={host} dbname=postgres user=postgres password=local-postgres connect_timeout=3",
                    kwargs={
                        "autocommit": True,
                        "options": "-c statement_timeout=120000 -c work_mem=32MB",
                    },
                    min_size=0,
                    max_size=8,
                    timeout=5,
                    configure=register_vector,
                )
            pool = self.pools[host]
        return pool.connection()

    def load(self):
        host = self.leader()
        # Type registration requires the extension to exist before opening the workload connection.
        with psycopg.connect(
            host=host,
            dbname="postgres",
            user="postgres",
            password="local-postgres",
            autocommit=True,
            connect_timeout=3,
        ) as c:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        start = time.perf_counter()
        with self.connect() as c:
            c.execute("DROP TABLE IF EXISTS inventory")
            c.execute("DROP TABLE IF EXISTS products")
            c.execute(
                "CREATE TABLE products(id bigint PRIMARY KEY,data jsonb NOT NULL,embedding vector(384) NOT NULL,terms tsvector GENERATED ALWAYS AS (to_tsvector('english',(data->>'name') || ' ' || (data->>'description'))) STORED)"
            )
            c.execute(
                "CREATE TABLE inventory(id bigint PRIMARY KEY REFERENCES products(id),tenant integer NOT NULL,stock integer NOT NULL,price_cents integer NOT NULL,version integer NOT NULL)"
            )
            with c.transaction():
                with c.cursor().copy(
                    "COPY products(id,data,embedding) FROM STDIN (FORMAT BINARY)"
                ) as copy:
                    copy.set_types(["int8", "jsonb", "vector"])
                    for i, row in enumerate(self.fixture.products):
                        text = {
                            k: row[k]
                            for k in META
                            if k
                            not in ["id", "tenant", "stock", "price_cents", "version"]
                        }
                        copy.write_row((i, Jsonb(text), self.fixture.vectors[i]))
                with c.cursor().copy(
                    "COPY inventory(id,tenant,stock,price_cents,version) FROM STDIN (FORMAT BINARY)"
                ) as copy:
                    copy.set_types(["int8", "int4", "int4", "int4", "int4"])
                    for i, row in enumerate(self.fixture.products):
                        copy.write_row(
                            (
                                i,
                                row["tenant"],
                                row["stock"],
                                row["price_cents"],
                                row["version"],
                            )
                        )
            c.execute("CREATE INDEX inventory_tenant ON inventory(tenant,id)")
            c.execute("CREATE INDEX products_terms ON products USING gin(terms)")
            loaded = time.perf_counter() - start
            start_index = time.perf_counter()
            c.execute("SET maintenance_work_mem='512MB'")
            c.execute(
                "CREATE INDEX products_hnsw ON products USING hnsw(embedding vector_cosine_ops) WITH(m=16,ef_construction=128)"
            )
            c.execute("ANALYZE products")
            c.execute("ANALYZE inventory")
            return {
                "bulk_load_seconds": loaded,
                "index_seconds": time.perf_counter() - start_index,
                "extensions": c.execute(
                    "SELECT extversion FROM pg_extension WHERE extname='vector'"
                ).fetchone()[0],
            }

    def update(self, rows):
        # A connection error after commit is ambiguous: the caller journals it and never replays.
        with self.connect() as c:
            with c.transaction():
                with c.cursor() as cur:
                    cur.executemany(
                        "UPDATE inventory SET stock=%s,price_cents=%s,version=%s WHERE id=%s",
                        [
                            (r["stock"], r["price_cents"], r["version"], int(r["id"]))
                            for r in rows
                        ],
                    )

    def search(self, text, vector, mode, k=10, ids=None, tenant=None):
        predicates = []
        args = []
        if ids is not None:
            predicates.append("p.id=ANY(%s)")
            args.append([int(x) for x in ids])
        if tenant is not None:
            predicates.append("i.tenant=%s")
            args.append(int(tenant))
        where = " AND ".join(predicates) or "true"
        with self.connect() as c:
            c.execute("SET hnsw.ef_search=256")
            c.execute("SET hnsw.iterative_scan='strict_order'")
            c.execute("SET hnsw.max_scan_tuples=50000")
            if mode == "dense":
                sql = f"SELECT p.id,p.embedding <=> %s AS distance FROM products p JOIN inventory i USING(id) WHERE {where} ORDER BY p.embedding <=> %s LIMIT %s"
                values = [vector, *args, vector, k]
            elif mode == "lexical":
                sql = f"SELECT p.id,ts_rank_cd(p.terms,websearch_to_tsquery('english',%s)) score FROM products p JOIN inventory i USING(id) WHERE {where} AND p.terms @@ websearch_to_tsquery('english',%s) ORDER BY score DESC,p.id LIMIT %s"
                values = [text, *args, text, k]
            else:
                sql = f"""WITH dense AS MATERIALIZED (SELECT p.id,row_number() OVER(ORDER BY p.embedding <=> %s) rank FROM products p JOIN inventory i USING(id) WHERE {where} ORDER BY p.embedding <=> %s LIMIT 100), lexical AS MATERIALIZED (SELECT p.id,row_number() OVER(ORDER BY ts_rank_cd(p.terms,websearch_to_tsquery('english',%s)) DESC,p.id) rank FROM products p JOIN inventory i USING(id) WHERE {where} AND p.terms @@ websearch_to_tsquery('english',%s) ORDER BY rank LIMIT 100) SELECT coalesce(d.id,l.id),coalesce(1.0/(60+d.rank),0)+coalesce(1.0/(60+l.rank),0) score FROM dense d FULL JOIN lexical l USING(id) ORDER BY score DESC,1 LIMIT %s"""
                values = [vector, *args, vector, text, *args, text, k]
            return [
                {"id": str(row[0]), "score": float(row[1])}
                for row in c.execute(sql, values).fetchall()
            ]

    def rows(self, host=None):
        with self.connect(host) as c:
            with c.transaction():
                with c.cursor(name="verify") as cur:
                    cur.execute(
                        "SELECT p.id,p.data,i.tenant,i.stock,i.price_cents,i.version FROM products p JOIN inventory i USING(id)"
                    )
                    for pid, data, tenant, stock, price, version in cur:
                        yield dict(
                            data,
                            id=str(pid),
                            tenant=tenant,
                            stock=stock,
                            price_cents=price,
                            version=version,
                        )

    def stats(self):
        result = {}
        for host in self.hosts:
            try:
                with self.connect(host) as c:
                    role, count, bytes_ = c.execute(
                        "SELECT pg_is_in_recovery(),(SELECT count(*) FROM products),pg_total_relation_size('products')+pg_total_relation_size('inventory')"
                    ).fetchone()
                    result[host] = {
                        "role": "replica" if role else "primary",
                        "rows": count,
                        "bytes": bytes_,
                    }
            except Exception as e:
                result[host] = {"error": str(e)}
        return result

    def verify_replicas(self, fixture, versions=None, attempted=None):
        results = {}
        for host in self.hosts:
            deadline = time.monotonic() + 120
            while True:
                try:

                    class View:
                        def rows(view):
                            return self.rows(host)

                    results[host] = oracle(View(), fixture, versions, attempted)
                    break
                except Exception:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
        return results

    def strength_test(self):
        tenant = self.fixture.products[self.fixture.exact[0]["ids"][0]]["tenant"]
        budget = 50100
        eligible = {
            int(row["id"])
            for row in self.rows()
            if row["tenant"] == tenant
            and row["stock"] > 0
            and row["price_cents"] <= budget
        }
        sql = "SELECT p.id FROM products p JOIN inventory i USING(id) WHERE i.tenant=%s AND i.stock>0 AND i.price_cents<=%s ORDER BY p.embedding <=> %s LIMIT 10"
        params = [tenant, budget, self.fixture.qvectors[0]]
        with self.connect() as c:
            c.execute("SET hnsw.ef_search=256")
            c.execute("SET hnsw.iterative_scan='strict_order'")
            before = [row[0] for row in c.execute(sql, params)]
            if len(before) != min(10, len(eligible)) or not set(before) <= eligible:
                raise RuntimeError("inventory join leaked or under-returned")
            target = before[0]
            original = c.execute(
                "SELECT stock FROM inventory WHERE id=%s", [target]
            ).fetchone()[0]
            with c.transaction():
                c.execute("UPDATE inventory SET stock=0 WHERE id=%s", [target])
                during = [row[0] for row in c.execute(sql, params)]
                if target in during:
                    raise RuntimeError("out-of-stock product remained eligible")
                raise psycopg.Rollback()
            if (
                c.execute(
                    "SELECT stock FROM inventory WHERE id=%s", [target]
                ).fetchone()[0]
                != original
            ):
                raise RuntimeError("inventory rollback leaked")
            after = [row[0] for row in c.execute(sql, params)]
            if target not in after:
                raise RuntimeError("rolled-back product did not regain eligibility")
            plan = c.execute(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT id FROM products ORDER BY embedding <=> %s LIMIT 10",
                [self.fixture.qvectors[0]],
            ).fetchone()[0]
            return {
                "rollback_preserved_stock": True,
                "inventory_join": {
                    "tenant": tenant,
                    "budget_cents": budget,
                    "eligible_products": len(eligible),
                    "returned": len(before),
                    "stock_zero_excluded_in_transaction": True,
                    "rollback_restored_eligibility": True,
                },
                "dense_plan": plan,
            }


if __name__ == "__main__":
    main(Adapter)
