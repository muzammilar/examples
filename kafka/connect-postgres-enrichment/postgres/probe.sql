ALTER TABLE customers ADD COLUMN IF NOT EXISTS cdc_probe bigint NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS probe_operations (
    id text PRIMARY KEY,
    version bigint UNIQUE NOT NULL
);
