-- One row per phase; 3 tablets, so some tablet leaders start on yb-3
CREATE TABLE IF NOT EXISTS failover (phase text, writes int DEFAULT 1, PRIMARY KEY (phase HASH))
  SPLIT INTO 3 TABLETS;
INSERT INTO failover (phase) VALUES ('before') ON CONFLICT (phase) DO UPDATE SET writes = failover.writes + 1;
