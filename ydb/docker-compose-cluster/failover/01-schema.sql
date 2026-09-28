CREATE TABLE IF NOT EXISTS failover (
    phase Utf8 NOT NULL,
    at Timestamp,
    PRIMARY KEY (phase)
);
