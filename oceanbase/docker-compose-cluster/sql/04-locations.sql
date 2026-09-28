USE demo;
-- Log streams of the `test` tenant: one replica per zone, one LEADER. The tenant's
-- PRIMARY_ZONE is 'zone1;zone2;zone3', so leaders stay in zone1 while it is up.
-- LS 1 is the tenant's system log stream, LS 1001 holds the user tables.
SELECT LS_ID, ZONE, SVR_IP, ROLE, REPLICA_TYPE, PAXOS_REPLICA_NUMBER, MEMBER_LIST
  FROM oceanbase.DBA_OB_LS_LOCATIONS
 ORDER BY LS_ID, ZONE;

-- Every partition (tablet) of the demo tables has a replica in each zone; only the
-- leader serves strong reads and writes.
SELECT TABLE_NAME, PARTITION_NAME, TABLET_ID, LS_ID, ZONE, SVR_IP, ROLE, REPLICA_TYPE
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' AND TABLE_TYPE = 'USER TABLE'
 ORDER BY TABLE_NAME, TABLET_ID, ZONE;

-- Replicas per zone and where the leaders are (demo tablets).
SELECT ZONE, COUNT(*) AS tablet_replicas, SUM(ROLE = 'LEADER') AS leaders
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' AND TABLE_TYPE = 'USER TABLE'
 GROUP BY ZONE ORDER BY ZONE;

-- Paxos state per replica: members, and whether each follower is in sync with its leader.
SELECT LS_ID, SVR_IP, ROLE, PAXOS_REPLICA_NUM, IN_SYNC, PAXOS_MEMBER_LIST
  FROM oceanbase.GV$OB_LOG_STAT
 ORDER BY LS_ID, SVR_IP;
