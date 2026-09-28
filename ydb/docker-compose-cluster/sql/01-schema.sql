-- start fresh on every run (DROP and CREATE are one DDL query)
DROP TABLE IF EXISTS kv;
CREATE TABLE kv (
    k Utf8 NOT NULL,
    v Utf8,
    PRIMARY KEY (k)
);
