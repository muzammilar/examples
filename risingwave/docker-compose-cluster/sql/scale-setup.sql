-- Load for `make scale-out` / `make scale-in`: a datagen source split 8 ways, generating faster
-- than the compute nodes can aggregate it, and an MV over it. Throughput = growth of sum(n).
DROP MATERIALIZED VIEW IF EXISTS gen_by_key;
DROP SOURCE IF EXISTS gen;
CREATE SOURCE gen (k BIGINT, v BIGINT) WITH (
    connector = 'datagen',
    fields.k.kind = 'random', fields.k.min = '0', fields.k.max = '99999',
    fields.v.kind = 'random', fields.v.min = '0', fields.v.max = '1000',
    datagen.split.num = '8',
    datagen.rows.per.second = '50000000'   -- far above what the nodes can process
) FORMAT PLAIN ENCODE JSON;
CREATE MATERIALIZED VIEW gen_by_key AS
SELECT k, count(*) AS n, sum(v) AS s FROM gen GROUP BY k;
