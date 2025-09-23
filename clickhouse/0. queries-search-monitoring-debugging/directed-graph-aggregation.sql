-- CLickHouse

DROP TABLE IF EXISTS my_graph;
DROP TABLE IF EXISTS my_graph_smt;
DROP TABLE IF EXISTS my_graph_smt_view;

CREATE TABLE my_graph (
    child UInt64,
    parents Array(UInt64), /* child -> parents */
) ENGINE = MergeTree()
ORDER BY child;

CREATE TABLE my_graph_smt (
    child UInt64,
    parent UInt64,
    cnt UInt64,
) ENGINE = SummingMergeTree()
ORDER BY (parent, child);

-- Materialized view

CREATE MATERIALIZED VIEW my_graph_smt_view TO my_graph_smt AS
SELECT arrayJoin(parents) AS parent, child, 1 AS cnt FROM my_graph;

-- Insert data

INSERT INTO my_graph (child, parents) VALUES (1, [2, 3, 6, 9]);
INSERT INTO my_graph (child, parents) VALUES (2, [3, 4, 5]);
INSERT INTO my_graph (child, parents) VALUES (3, [4, 5, 6, 7]);
INSERT INTO my_graph (child, parents) VALUES (4, [7, 8, 9]);
INSERT INTO my_graph (child, parents) VALUES (5, [7, 8, 9]);
INSERT INTO my_graph (child, parents) VALUES (6, [9]);
INSERT INTO my_graph (child, parents) VALUES (7, [9]);
INSERT INTO my_graph (child, parents) VALUES (8, []);
INSERT INTO my_graph (child, parents) VALUES (9, []);
INSERT INTO my_graph (child, parents) VALUES (10, []);

-- Query

SELECT * FROM my_graph;
SELECT * FROM my_graph_smt;
SELECT parent, sum(cnt) FROM my_graph_smt GROUP BY parent;
