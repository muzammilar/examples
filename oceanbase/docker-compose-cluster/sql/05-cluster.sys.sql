-- *.sys.sql files run as root@sys: cluster-wide views live in the sys tenant.
SELECT ZONE, STATUS FROM oceanbase.DBA_OB_ZONES ORDER BY ZONE;

SELECT SVR_IP, SVR_PORT, ZONE, STATUS, WITH_ROOTSERVER, BUILD_VERSION
  FROM oceanbase.DBA_OB_SERVERS ORDER BY ZONE;

SELECT TENANT_ID, TENANT_NAME, TENANT_TYPE, COMPATIBILITY_MODE, STATUS, LOCALITY, PRIMARY_ZONE
  FROM oceanbase.DBA_OB_TENANTS ORDER BY TENANT_ID;

-- Per-server resources: memory_limit / CPU each observer runs with, and what the units take.
SELECT SVR_IP, ZONE, CPU_CAPACITY, CPU_ASSIGNED,
       ROUND(MEM_CAPACITY / 1024 / 1024 / 1024, 1) AS mem_capacity_gb,
       ROUND(MEM_ASSIGNED / 1024 / 1024 / 1024, 1) AS mem_assigned_gb,
       ROUND(DATA_DISK_CAPACITY / 1024 / 1024 / 1024, 1) AS data_disk_gb,
       ROUND(LOG_DISK_CAPACITY / 1024 / 1024 / 1024, 1) AS log_disk_gb
  FROM oceanbase.GV$OB_SERVERS ORDER BY ZONE;

-- One unit per tenant per zone (UNIT_NUM = 1, ZONE_LIST zone1..zone3).
SELECT t.TENANT_NAME, u.ZONE, u.SVR_IP, u.STATUS, u.MAX_CPU,
       ROUND(u.MEMORY_SIZE / 1024 / 1024 / 1024, 1) AS memory_gb,
       ROUND(u.LOG_DISK_SIZE / 1024 / 1024 / 1024, 1) AS log_disk_gb
  FROM oceanbase.DBA_OB_UNITS u JOIN oceanbase.DBA_OB_TENANTS t ON t.TENANT_ID = u.TENANT_ID
 ORDER BY u.TENANT_ID, u.ZONE;

-- Leaders per zone across all tenants' log streams (sys, META$1002, test).
SELECT l.TENANT_ID, t.TENANT_NAME, l.LS_ID,
       MAX(CASE WHEN l.ROLE = 'LEADER' THEN l.ZONE END) AS leader_zone,
       GROUP_CONCAT(l.ZONE ORDER BY l.ZONE) AS replica_zones
  FROM oceanbase.CDB_OB_LS_LOCATIONS l JOIN oceanbase.DBA_OB_TENANTS t ON t.TENANT_ID = l.TENANT_ID
 GROUP BY l.TENANT_ID, t.TENANT_NAME, l.LS_ID
 ORDER BY l.TENANT_ID, l.LS_ID;
