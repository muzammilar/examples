-- Replicas of every log stream of tenant `test` (one FULL replica per zone, one LEADER).
-- LS 1 is the tenant's system log stream; LS 1001 (and, after scale-out, a second user log
-- stream on the new units) holds the user tablets. Leaders stay in zone1 (PRIMARY_ZONE
-- 'zone1;zone2;zone3'), so after scale-out there is a leader on ob1 and one on ob4.
SELECT LS_ID, ZONE, SVR_IP, ROLE, REPLICA_TYPE, PAXOS_REPLICA_NUMBER
  FROM oceanbase.DBA_OB_LS_LOCATIONS
 ORDER BY LS_ID, ZONE, SVR_IP;

-- Tablet replicas and leaders per server (demo tables).
SELECT SVR_IP, ZONE, COUNT(*) AS tablet_replicas, SUM(ROLE = 'LEADER') AS leaders
  FROM oceanbase.DBA_OB_TABLE_LOCATIONS
 WHERE DATABASE_NAME = 'demo' AND TABLE_TYPE = 'USER TABLE'
 GROUP BY SVR_IP, ZONE ORDER BY ZONE, SVR_IP;

-- Paxos state: every follower in sync with its leader.
SELECT LS_ID, SVR_IP, ROLE, PAXOS_REPLICA_NUM, IN_SYNC
  FROM oceanbase.GV$OB_LOG_STAT
 ORDER BY LS_ID, SVR_IP;
